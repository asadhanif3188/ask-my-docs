"""Per-user rate limiting on POST /query (app/rate_limit.py, app/api/query.py).

check_rate_limit() is pure in-process state, so most of this suite calls it
directly rather than going through the DB-backed HTTP stack (mirrors the
project's own preference for isolating unrelated concerns — see
tests/test_db_outage.py, tests/test_token_budget.py). One HTTP-level test at
the bottom exercises the full 429 + Retry-After response shape, matching the
acceptance criterion's "a for-loop of curls trips the limiter live".
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import rate_limit
from app.api import query as query_api
from app.auth import Principal, get_principal
from app.main import app
from app.models import Answer, Citation, Claim, RetrievedChunk
from app.retrieval.rewrite import RewrittenQuery


@pytest.fixture(autouse=True)
def _reset_buckets():
    """Module-level _buckets state persists across tests in the same process
    (that's the whole point of an in-memory limiter) — must be cleared so
    tests don't leak counts into each other."""
    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


@pytest.fixture
def small_limit(monkeypatch):
    """3 requests/min, independent of get_settings()'s real (env-driven)
    default so this suite doesn't depend on config.py's value."""
    monkeypatch.setattr(rate_limit, "get_settings", lambda: SimpleNamespace(rate_limit_per_minute=3))


# --- check_rate_limit -----------------------------------------------------


def test_burst_past_limit_returns_not_allowed_with_positive_retry_after(small_limit):
    for _ in range(3):
        allowed, retry_after = rate_limit.check_rate_limit(user_id=1)
        assert allowed is True
        assert retry_after == 0

    allowed, retry_after = rate_limit.check_rate_limit(user_id=1)

    assert allowed is False
    assert retry_after > 0


def test_window_expiry_resets_the_counter(small_limit, monkeypatch):
    fake_now = [1_000_000.0]
    monkeypatch.setattr(rate_limit.time, "time", lambda: fake_now[0])

    for _ in range(3):
        assert rate_limit.check_rate_limit(user_id=1)[0] is True
    assert rate_limit.check_rate_limit(user_id=1)[0] is False  # 4th in-window request blocked

    fake_now[0] += rate_limit.WINDOW_SECONDS  # roll into the next fixed window

    allowed, retry_after = rate_limit.check_rate_limit(user_id=1)
    assert allowed is True
    assert retry_after == 0


def test_user_a_throttled_does_not_affect_user_b_in_same_org(small_limit):
    for _ in range(3):
        assert rate_limit.check_rate_limit(user_id=1)[0] is True
    assert rate_limit.check_rate_limit(user_id=1)[0] is False  # user A now over budget

    # user B, same org, untouched bucket
    assert rate_limit.check_rate_limit(user_id=2)[0] is True


# --- HTTP-level: /query returns 429 + Retry-After once tripped ------------


def _client_for(user_id: int, org_id: int) -> httpx.AsyncClient:
    app.dependency_overrides[get_principal] = lambda: Principal(user_id=user_id, org_id=org_id)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _stub_rewrite(monkeypatch):
    async def no_op_rewrite(question: str) -> RewrittenQuery:
        return RewrittenQuery(dense=question, fts=question)

    monkeypatch.setattr(query_api, "rewrite_query", no_op_rewrite)


@pytest.fixture
def _stub_downstream(monkeypatch):
    """Everything past the rate limiter, stubbed so a request that clears the
    limiter completes without a real DB/LLM call — same isolation reasoning
    as test_db_outage.py's _stub_budget_check (no `pool` fixture here, so a
    lazily-created real pool would otherwise leak across event loops) and
    test_token_budget.py's _stub_pipeline. Returning the mocks lets the test
    assert *how many times* the budget check ran, which is what actually
    proves ordering (rate limit short-circuits before it, not just "some
    429 happened").
    """
    usage_mock = AsyncMock(return_value=0)
    limit_mock = AsyncMock(return_value=1_000_000)
    monkeypatch.setattr(query_api, "usage_today", usage_mock)
    monkeypatch.setattr(query_api, "daily_limit", limit_mock)

    chunk = RetrievedChunk(
        chunk_id=1, document_id=1, page=1, text="some text",
        context_summary="summary", score=0.9,
    )

    async def ok_retrieve(*args, **kwargs):
        return [chunk]

    async def ok_generate(*args, **kwargs):
        return Answer(claims=[Claim(
            text="An answer.",
            citations=[Citation(chunk_id=1, document_id=1, page=1, quote="some text")],
        )])

    monkeypatch.setattr(query_api, "hybrid_retrieve", ok_retrieve)
    monkeypatch.setattr(query_api, "rerank", lambda q, c, top_k: c)
    monkeypatch.setattr(query_api, "generate_answer", ok_generate)
    return usage_mock


async def test_query_returns_429_with_retry_after_once_limit_is_tripped(small_limit, _stub_downstream):
    usage_mock = _stub_downstream

    async with _client_for(user_id=1, org_id=1) as c:
        for _ in range(3):
            ok_response = await c.post("/v1/query", json={"question": "well within the limit"})
            assert ok_response.status_code == 200

        response = await c.post("/v1/query", json={"question": "one too many"})

    app.dependency_overrides.clear()

    assert response.status_code == 429
    assert response.json()["detail"]
    assert int(response.headers["retry-after"]) > 0
    # order of checks: rate limit before the budget query — the 4th (blocked)
    # request must never have reached usage_today.
    assert usage_mock.await_count == 3
