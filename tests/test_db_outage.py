"""A total Postgres outage must fail honestly and in JSON.

Contract (see INCIDENTS.md, "total DB outage isn't covered by the degraded
fallback"): RetrievalDegraded exists for a *partial* failure — the dense branch
dies, FTS still answers, so we serve a flagged 200. When Postgres itself is
unreachable the FTS fallback dies too, so there is nothing to degrade *to*:
that must surface as a 503 (retryable, alertable), never a 200 pretending no
documents matched, and never a plain-text 500.
"""

import httpx
import pytest

from app.api import health as health_api
from app.api import query as query_api
from app.auth import Principal, get_principal
from app.main import app
from app.models import RetrievedChunk
from app.retrieval import hybrid
from app.retrieval.hybrid import RetrievalUnavailable, hybrid_retrieve
from app.retrieval.rewrite import RewrittenQuery

DB_DOWN = ConnectionRefusedError("[WinError 1225] The remote computer refused the network connection")


@pytest.fixture
def client():
    app.dependency_overrides[get_principal] = lambda: Principal(user_id=1, org_id=1)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_rewrite(monkeypatch):
    """These tests are about the retrieval/generation failure paths, not rewrite.

    Without this, every client.post("/v1/query") below reaches the real
    rewrite_query() (enable_rewrite defaults on) and attempts a live Anthropic
    call before the mocked hybrid_retrieve is ever hit — see INCIDENTS.md on
    tests that silently bill (or hang on) the real API.
    """

    async def no_op_rewrite(question: str) -> RewrittenQuery:
        return RewrittenQuery(dense=question, fts=question)

    monkeypatch.setattr(query_api, "rewrite_query", no_op_rewrite)


async def test_hybrid_retrieve_raises_unavailable_when_fts_branch_is_down(monkeypatch):
    """FTS is the fallback path itself — if it dies, degrading is not an option."""

    async def dead_fts(*args, **kwargs):
        raise DB_DOWN

    monkeypatch.setattr(hybrid, "_fts_search", dead_fts)

    with pytest.raises(RetrievalUnavailable):
        await hybrid_retrieve(org_id=1, dense_query="anything", fts_query="anything", top_k=5)


async def test_query_returns_503_json_when_database_is_unreachable(client, monkeypatch):
    async def dead_retrieve(*args, **kwargs):
        raise RetrievalUnavailable(str(DB_DOWN))

    monkeypatch.setattr(query_api, "hybrid_retrieve", dead_retrieve)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]  # structured, not a plain-text stack trace


async def test_query_503_body_does_not_leak_internals(client, monkeypatch):
    async def dead_retrieve(*args, **kwargs):
        raise RetrievalUnavailable("postgresql://rag:rag@localhost:5432/askmydocs refused")

    monkeypatch.setattr(query_api, "hybrid_retrieve", dead_retrieve)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    body = response.text
    assert "postgresql://" not in body  # no DSN/credentials
    assert "Traceback" not in body


async def test_healthz_returns_503_json_when_database_is_unreachable(client, monkeypatch):
    async def dead_pool():
        raise DB_DOWN

    monkeypatch.setattr(health_api, "get_pool", dead_pool)

    async with client as c:
        response = await c.get("/healthz")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "unavailable"


async def test_generation_failure_reason_is_logged(client, monkeypatch, caplog):
    """The GenerationUnavailable branch tells the user only "unavailable". If it
    also stays silent in the logs, a bug in our own validator is indistinguishable
    from a dead upstream — which is exactly what happened twice (INCIDENTS.md)."""
    from app.generation.generate import GenerationUnavailable

    async def ok_retrieve(*args, **kwargs):
        return [
            RetrievedChunk(
                chunk_id=1, document_id=1, page=1, text="some text",
                context_summary="summary", score=0.9,
            )
        ]

    async def dead_generate(*args, **kwargs):
        raise GenerationUnavailable("Output failed citation validation: quote not found")

    monkeypatch.setattr(query_api, "hybrid_retrieve", ok_retrieve)
    monkeypatch.setattr(query_api, "rerank", lambda q, c, top_k: c)
    monkeypatch.setattr(query_api, "generate_answer", dead_generate)

    with caplog.at_level("WARNING", logger="app.api.query"):
        async with client as c:
            response = await c.post("/v1/query", json={"question": "What was the revenue?"})

    assert response.status_code == 200  # fallback contract: sources still returned
    assert response.json()["answer"] is None
    assert any("citation validation" in r.getMessage() for r in caplog.records), (
        "the real cause must reach the logs, not just a generic user-facing detail"
    )


async def test_unhandled_exception_returns_json_not_plain_text(client, monkeypatch):
    """Defense in depth: no route should ever fall through to Starlette's
    plain-text 'Internal Server Error', which breaks the API's JSON contract."""

    async def boom(*args, **kwargs):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(query_api, "hybrid_retrieve", boom)

    async with client as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]
    assert "something nobody anticipated" not in response.text  # no internals leaked
