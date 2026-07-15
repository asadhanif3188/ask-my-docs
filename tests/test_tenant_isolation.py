"""THE key security test: tenant A must never retrieve tenant B's chunks.

Isolation is enforced in SQL (org_id filter inside hybrid.py), so we test the
retrieval functions directly rather than mocking — plus one HTTP-level test
that goes through a real JWT and the actual /v1/query route, so both the SQL
boundary and the API boundary are independently proven (belt and suspenders).

This file, together with tests/test_ingestion_lifecycle.py's delete-propagation
coverage, is THE "tenant A cannot read tenant B" artifact for the build plan's
security requirement — see the README security section.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest

from app import rate_limit
from app.api import query as query_api
from app.auth import get_principal
from app.config import get_settings
from app.main import app
from app.models import Answer, Citation, Claim
from app.retrieval import hybrid
from app.retrieval.hybrid import _dense_search, _fts_search, hybrid_retrieve
from app.retrieval.rewrite import RewrittenQuery
from tests.conftest import QUERY_VECTOR


async def test_fts_search_is_tenant_scoped(two_orgs):
    org_a, org_b = two_orgs

    results_a = await _fts_search(org_a, "secret revenue figure", limit=10)
    results_b = await _fts_search(org_b, "secret revenue figure", limit=10)

    assert results_a, "org A should find its own chunk"
    assert all("test-org-a" in c.text for c in results_a)
    assert all("test-org-b" not in c.text for c in results_a)
    assert all("test-org-a" not in c.text for c in results_b)


@pytest.fixture
def _fake_embed_query(monkeypatch):
    """Stands in for the real BGE-M3 model (see tests/conftest.py QUERY_VECTOR):
    always returns the vector that org B's fixture chunk was seeded to match
    exactly, regardless of the query text."""

    async def fake(text: str) -> list[float]:
        return QUERY_VECTOR

    monkeypatch.setattr(hybrid, "embed_query", fake)


async def test_dense_search_is_tenant_scoped(two_orgs_with_embeddings, _fake_embed_query, pool):
    org_a, org_b = two_orgs_with_embeddings
    org_b_chunk_id = await pool.fetchval("SELECT id FROM chunks WHERE org_id = $1", org_b)

    results_a = await _dense_search(org_a, "secret revenue figure", limit=10)
    results_b = await _dense_search(org_b, "secret revenue figure", limit=10)

    assert results_a, "org A should find its own chunk"
    assert all("test-org-a" in c.text for c in results_a)
    assert all("test-org-b" not in c.text for c in results_a)
    assert org_b_chunk_id not in {c.chunk_id for c in results_a}, (
        "org B's vector was engineered as the nearest neighbor to the query, so it "
        "would be the first row to leak in if the org_id filter were ever dropped"
    )
    assert all("test-org-a" not in c.text for c in results_b)


async def test_hybrid_retrieve_is_tenant_scoped(two_orgs_with_embeddings, _fake_embed_query, pool):
    """Same guarantee through the full fused retrieval path (FTS + dense + RRF)."""
    org_a, org_b = two_orgs_with_embeddings
    org_b_chunk_id = await pool.fetchval("SELECT id FROM chunks WHERE org_id = $1", org_b)

    results_a = await hybrid_retrieve(
        org_a, dense_query="secret revenue figure", fts_query="secret revenue figure", top_k=10
    )

    assert results_a, "org A should find its own chunk"
    assert all("test-org-a" in c.text for c in results_a)
    assert all("test-org-b" not in c.text for c in results_a)
    assert org_b_chunk_id not in {c.chunk_id for c in results_a}


@pytest.fixture
def _stub_non_retrieval(monkeypatch):
    """Everything in the /query route except retrieval itself, stubbed so this
    test exercises the real hybrid_retrieve → real SQL path while avoiding a
    live LLM call or a real reranker model load (same reasoning as
    tests/test_rate_limit.py's _stub_downstream and tests/test_db_outage.py's
    _stub_budget_check)."""

    async def no_op_rewrite(question: str) -> RewrittenQuery:
        return RewrittenQuery(dense=question, fts=question)

    monkeypatch.setattr(query_api, "rewrite_query", no_op_rewrite)
    monkeypatch.setattr(query_api, "usage_today", AsyncMock(return_value=0))
    monkeypatch.setattr(query_api, "daily_limit", AsyncMock(return_value=1_000_000))
    monkeypatch.setattr(query_api, "rerank", lambda q, c, top_k: c)

    async def stub_generate(question, chunks, *, degraded, org_id):
        if not chunks:
            return Answer(claims=[])
        return Answer(claims=[Claim(
            text="stub answer",
            citations=[Citation(
                chunk_id=chunks[0].chunk_id, document_id=chunks[0].document_id,
                page=chunks[0].page, quote=chunks[0].text[:20],
            )],
        )])

    monkeypatch.setattr(query_api, "generate_answer", stub_generate)


def _jwt_for(org_id: int, user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "org_id": org_id, "iat": now, "exp": now + timedelta(minutes=5)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.fixture
def _reset_rate_limit_bucket():
    """This test hits the real /v1/query route, which runs the real (unstubbed)
    check_rate_limit() — a module-global in-memory counter keyed by user_id
    that otherwise persists across the whole pytest process (see
    tests/test_rate_limit.py's _reset_buckets for the same concern). Without
    this, a bucket left over from another suite sharing this user_id could
    trip a 429 here and turn the flagship security test flaky."""
    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


async def test_query_endpoint_is_tenant_scoped_with_real_jwt(
    two_orgs_with_embeddings, _fake_embed_query, _stub_non_retrieval, _reset_rate_limit_bucket
):
    """API-level mirror of test_hybrid_retrieve_is_tenant_scoped: a real JWT for
    org A (minted and verified exactly like production, no dependency_overrides
    bypassing app.auth.get_principal) must never surface org B's chunk through
    the actual /v1/query route."""
    assert get_principal not in app.dependency_overrides, (
        "this test's whole point is exercising the real JWT auth path — a leftover "
        "override from another test would silently defeat that"
    )
    org_a, _org_b = two_orgs_with_embeddings
    token_a = _jwt_for(org_a, user_id=org_a)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/query",
            json={"question": "secret revenue figure"},
            headers={"Authorization": f"Bearer {token_a}"},
        )

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert sources, "org A should find its own chunk"
    assert all("test-org-a" in s["text"] for s in sources)
    assert all("test-org-b" not in s["text"] for s in sources)


async def test_org_id_never_read_from_request_body():
    """Guard against regression: the query endpoint takes org_id from the JWT
    principal only. QueryRequest must not accept an org_id field."""
    from app.models import QueryRequest

    assert "org_id" not in QueryRequest.model_fields
