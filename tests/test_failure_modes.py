"""Pins the failure contracts claimed in hybrid.py, generate.py, and query.py
under induced failure, not just documented in their docstrings.

Companion to tests/test_db_outage.py (total DB outage -> 503) and
tests/test_generation.py (the repair-round contract) -- this file covers the
*partial*-failure and *composite*-failure paths those don't:

1. Dense search fails            -> FTS-only fallback, response flagged degraded.
2. LLM unavailable                -> sources returned, HTTP 200, never a 500.
3. Both fail at once (composite)  -> still a structured 200 with sources AND
   both signals present simultaneously.
4. Malformed PDF at ingest        -> quarantined with a reason, job still
   completes (quarantining is a valid outcome, not a job failure).
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from anthropic import AuthenticationError

from app.api import query as query_api
from app.auth import Principal, get_principal
from app.generation import generate as generate_module
from app.generation.generate import GenerationUnavailable
from app.ingestion import jobs, pipeline
from app.main import app
from app.models import Answer, Citation, Claim, RetrievedChunk
from app.retrieval import hybrid
from app.retrieval.hybrid import RetrievalDegraded, hybrid_retrieve
from app.retrieval.rewrite import RewrittenQuery

CHUNK = RetrievedChunk(
    chunk_id=1, document_id=1, page=1,
    text="Total net sales $391,035 million for fiscal year 2024.",
    context_summary="Apple Inc. 10-K fiscal 2024 results", score=0.9,
)


@pytest.fixture
def client():
    app.dependency_overrides[get_principal] = lambda: Principal(user_id=1, org_id=1)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_rewrite(monkeypatch):
    """These tests are about retrieval/generation failure paths, not rewrite
    (see INCIDENTS.md on tests that silently reach the real API)."""

    async def no_op_rewrite(question: str) -> RewrittenQuery:
        return RewrittenQuery(dense=question, fts=question)

    monkeypatch.setattr(query_api, "rewrite_query", no_op_rewrite)


@pytest.fixture(autouse=True)
def _stub_budget_check(monkeypatch):
    async def no_usage(org_id: int) -> int:
        return 0

    async def generous_limit(org_id: int) -> int:
        return 1_000_000

    monkeypatch.setattr(query_api, "usage_today", no_usage)
    monkeypatch.setattr(query_api, "daily_limit", generous_limit)


@pytest.fixture(autouse=True)
def _stub_rerank(monkeypatch):
    monkeypatch.setattr(query_api, "rerank", lambda q, c, top_k: c)


def _auth_error() -> AuthenticationError:
    """A realistic 401 the SDK would raise for a bad LLM_API_KEY, not a bare
    RuntimeError -- generate.py's except clause is broad, but the point of
    this test is pinning the *actual* failure shape, not any exception."""
    response = httpx.Response(
        status_code=401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    return AuthenticationError(
        "invalid x-api-key",
        response=response,
        body={"error": {"type": "authentication_error", "message": "invalid x-api-key"}},
    )


# ---------------------------------------------------------------------------
# 1. Dense search fails -> FTS-only fallback, response flagged degraded
# ---------------------------------------------------------------------------


async def test_dense_search_failure_raises_retrieval_degraded_with_fts_fallback(monkeypatch):
    """Unit level: hybrid_retrieve's contract in isolation."""

    async def ok_fts(*args, **kwargs):
        return [CHUNK]

    async def dead_dense(*args, **kwargs):
        raise RuntimeError("embedding model unreachable")

    monkeypatch.setattr(hybrid, "_fts_search", ok_fts)
    monkeypatch.setattr(hybrid, "_dense_search", dead_dense)

    with pytest.raises(RetrievalDegraded) as exc_info:
        await hybrid_retrieve(org_id=1, dense_query="q", fts_query="q", top_k=5)

    assert exc_info.value.fallback_results == [CHUNK]


async def test_query_endpoint_serves_degraded_flagged_response_when_dense_search_fails(
    client, monkeypatch
):
    """API level: the response must set BOTH the top-level `degraded` flag and
    `answer.degraded`, and must still return the FTS-only sources -- never a
    500, never a response that looks like nothing matched."""

    async def degraded_retrieve(*args, **kwargs):
        raise RetrievalDegraded(fallback_results=[CHUNK])

    async def stub_generate(question, chunks, *, degraded, org_id):
        return Answer(
            claims=[Claim(
                text="stub answer",
                citations=[Citation(
                    chunk_id=chunks[0].chunk_id, document_id=chunks[0].document_id,
                    page=chunks[0].page, quote=chunks[0].text[:10],
                )],
            )],
            degraded=degraded,
        )

    monkeypatch.setattr(query_api, "hybrid_retrieve", degraded_retrieve)
    monkeypatch.setattr(query_api, "generate_answer", stub_generate)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["answer"]["degraded"] is True
    assert body["sources"], "FTS-only fallback sources must still be returned"


# ---------------------------------------------------------------------------
# 2. LLM unavailable -> sources returned, HTTP 200, never a 500
# ---------------------------------------------------------------------------


async def test_query_returns_200_with_sources_when_llm_api_key_is_invalid(client, monkeypatch):
    """Induces the failure with a realistic anthropic.AuthenticationError,
    exercised through the real generate_answer -> _call_llm path (not a stub of
    generate_answer itself), so this pins query.py's actual exception handling
    of a real upstream failure shape, not just the contract's shape in the abstract."""

    async def ok_retrieve(*args, **kwargs):
        return [CHUNK]

    llm_client = MagicMock()
    llm_client.messages.create = AsyncMock(side_effect=_auth_error())

    monkeypatch.setattr(query_api, "hybrid_retrieve", ok_retrieve)
    monkeypatch.setattr(generate_module, "AsyncAnthropic", lambda **kw: llm_client)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["sources"], "retrieved passages must still be returned"
    assert body["detail"] == "Sources found but answer generation is temporarily unavailable"
    assert body["degraded"] is False


# ---------------------------------------------------------------------------
# 3. Composite failure: dense search AND generation both down
# ---------------------------------------------------------------------------


async def test_composite_failure_still_returns_structured_200_with_both_signals(
    client, monkeypatch
):
    """The gap the task brief predicted as 'likely untested': before this test
    (and the accompanying fix -- see INCIDENTS.md), QueryResponse had no
    top-level `degraded` field. When generation also failed, `answer` was None,
    so there was no Answer left to carry `degraded` and the retrieval-degraded
    signal was silently dropped. A client would see only "generation
    unavailable" with no way to know retrieval was ALSO running on the
    FTS-only fallback."""

    async def degraded_retrieve(*args, **kwargs):
        raise RetrievalDegraded(fallback_results=[CHUNK])

    async def dead_generate(question, chunks, *, degraded, org_id):
        raise GenerationUnavailable("entailment check unavailable: judge is down")

    monkeypatch.setattr(query_api, "hybrid_retrieve", degraded_retrieve)
    monkeypatch.setattr(query_api, "generate_answer", dead_generate)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] is None
    assert body["sources"], "FTS-only fallback sources must still be returned"
    assert body["detail"] == "Sources found but answer generation is temporarily unavailable"
    assert body["degraded"] is True, (
        "composite failure must still report degraded retrieval, not just "
        "generation-unavailable"
    )


# ---------------------------------------------------------------------------
# 4. Malformed PDF at ingest -> quarantined with reason
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_ingestion_llm_calls(monkeypatch):
    """A malformed PDF is quarantined before summarization/embedding are ever
    reached, but stub them anyway so this file never risks a live call if that
    ordering ever changes underneath it."""

    async def fake_summarize(full_text, org_id=None):
        return "unused"

    def fake_embed(texts):
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(pipeline, "summarize_document", fake_summarize)
    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)


async def _make_org_and_document(pool, source_uri: str) -> tuple[int, int]:
    async with pool.acquire() as conn:
        org_id = await conn.fetchval(
            "INSERT INTO orgs (name) VALUES ($1) "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            "test-failure-modes-org",
        )
        doc_id = await conn.fetchval(
            "INSERT INTO documents (org_id, source_uri, content_hash, status) "
            "VALUES ($1, $2, 'pending-hash', 'pending') "
            "ON CONFLICT (org_id, source_uri) DO UPDATE SET status='pending' RETURNING id",
            org_id, source_uri,
        )
    return org_id, doc_id


async def test_malformed_pdf_is_quarantined_with_reason_through_the_full_job(pool, tmp_path):
    """parse_pdf() raising ValueError on unparseable input is already pinned
    directly (tests/test_ingestion_lifecycle.py). What isn't pinned anywhere is
    that a malformed PDF, ingested through the real worker path
    (process_job -> pipeline.ingest_document), actually ends up quarantined
    with a human-readable reason -- and that the *job* still completes, since
    quarantining is a valid outcome for process_job, not an exception it raises."""
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"this is not a pdf")

    org_id, doc_id = await _make_org_and_document(pool, str(bad))
    async with pool.acquire() as conn:
        job_id = await conn.fetchval(
            "INSERT INTO ingestion_jobs (document_id, kind) VALUES ($1, 'ingest') RETURNING id",
            doc_id,
        )

    async with pool.acquire() as conn, conn.transaction():
        job = await jobs.claim_next_job(conn)
        assert job is not None, "expected the queued job to be claimable"
        await jobs.process_job(conn, job)
        await conn.execute(
            "UPDATE ingestion_jobs SET status='done', finished_at=now() WHERE id=$1", job["id"]
        )

    async with pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status, error FROM documents WHERE id=$1", doc_id)
        job_row = await conn.fetchrow("SELECT status FROM ingestion_jobs WHERE id=$1", job_id)
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)

    assert doc_row["status"] == "quarantined"
    assert doc_row["error"] and "Unparseable PDF" in doc_row["error"]
    assert job_row["status"] == "done", "quarantining is a valid outcome, not a job failure"
