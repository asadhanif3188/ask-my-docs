"""Per-org token budget: recording after each LLM call, and the advisory-before
budget gate in the /query path (app/token_usage.py, app/api/query.py).

Uses a real Postgres connection (see conftest.py) for the recording/budget
functions themselves, and the same mocked-pipeline pattern as
tests/test_db_outage.py for the HTTP-level 429 behavior — no real LLM call is
ever made here.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.api import query as query_api
from app.auth import Principal, get_principal
from app.main import app
from app.models import RetrievedChunk
from app.retrieval.rewrite import RewrittenQuery
from app.token_usage import (
    PURPOSE_GENERATION,
    PURPOSE_SUMMARY,
    daily_limit,
    record_usage,
    usage_today,
)


@pytest.fixture
async def budget_orgs(pool, two_orgs):
    """two_orgs, plus teardown of any token_usage rows this suite writes for
    them — required because token_usage.org_id has no ON DELETE CASCADE, so
    two_orgs' own teardown (DELETE FROM orgs) would otherwise fail with a
    foreign-key violation."""
    org_a, org_b = two_orgs
    yield org_a, org_b
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM token_usage WHERE org_id = ANY($1::bigint[])", [org_a, org_b]
        )


def _client_for(org_id: int) -> httpx.AsyncClient:
    app.dependency_overrides[get_principal] = lambda: Principal(user_id=1, org_id=org_id)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _stub_rewrite(monkeypatch):
    """These tests are about the budget gate, not query rewriting — see the
    identical fixture in test_db_outage.py."""

    async def no_op_rewrite(question: str) -> RewrittenQuery:
        return RewrittenQuery(dense=question, fts=question)

    monkeypatch.setattr(query_api, "rewrite_query", no_op_rewrite)


@pytest.fixture
def _stub_pipeline(monkeypatch):
    """Stub retrieval/rerank/generation so an under-budget request reaches a 200
    without a real LLM or embedding call."""
    chunk = RetrievedChunk(
        chunk_id=1, document_id=1, page=1, text="some text",
        context_summary="summary", score=0.9,
    )

    async def ok_retrieve(*args, **kwargs):
        return [chunk]

    from app.models import Answer, Citation, Claim

    async def ok_generate(*args, **kwargs):
        return Answer(claims=[Claim(
            text="An answer.",
            citations=[Citation(chunk_id=1, document_id=1, page=1, quote="some text")],
        )])

    monkeypatch.setattr(query_api, "hybrid_retrieve", ok_retrieve)
    monkeypatch.setattr(query_api, "rerank", lambda q, c, top_k: c)
    monkeypatch.setattr(query_api, "generate_answer", ok_generate)


# --- record_usage / usage_today / daily_limit --------------------------------


async def test_record_usage_accumulates_across_calls(budget_orgs):
    org_a, _ = budget_orgs

    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=100, output_tokens=50)
    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=10, output_tokens=5)

    assert await usage_today(org_a) == 165


async def test_record_usage_keeps_purposes_separate_but_summed_by_usage_today(budget_orgs):
    org_a, _ = budget_orgs

    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=100, output_tokens=0)
    await record_usage(org_id=org_a, purpose=PURPOSE_SUMMARY, input_tokens=20, output_tokens=0)

    assert await usage_today(org_a) == 120


async def test_two_orgs_usage_is_independent(budget_orgs):
    org_a, org_b = budget_orgs

    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=500, output_tokens=0)

    assert await usage_today(org_a) == 500
    assert await usage_today(org_b) == 0


async def test_daily_limit_reads_the_per_org_override(pool, budget_orgs):
    org_a, org_b = budget_orgs
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orgs SET daily_token_budget = 12345 WHERE id = $1", org_a)

    assert await daily_limit(org_a) == 12345
    assert await daily_limit(org_b) != 12345  # untouched org keeps its own value


# --- /query budget gate --------------------------------------------------


async def test_org_at_budget_gets_429_with_structured_body(pool, budget_orgs, _stub_pipeline):
    org_a, _ = budget_orgs
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orgs SET daily_token_budget = 100 WHERE id = $1", org_a)
    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=100, output_tokens=0)

    async with _client_for(org_a) as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})
    app.dependency_overrides.clear()

    assert response.status_code == 429
    body = response.json()
    assert body["detail"]
    assert body["usage_today"] == 100
    assert body["daily_limit"] == 100


async def test_org_under_budget_passes(pool, budget_orgs, _stub_pipeline):
    org_a, _ = budget_orgs
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orgs SET daily_token_budget = 100000 WHERE id = $1", org_a)
    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=10, output_tokens=5)

    async with _client_for(org_a) as c:
        response = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})
    app.dependency_overrides.clear()

    assert response.status_code == 200


async def test_orgs_budget_gate_is_tenant_isolated(pool, budget_orgs, _stub_pipeline):
    """Org A pinned at its limit must 429; org B, untouched, must still pass —
    tenant isolation applies to spend, not just document retrieval."""
    org_a, org_b = budget_orgs
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orgs SET daily_token_budget = 50 WHERE id = $1", org_a)
    await record_usage(org_id=org_a, purpose=PURPOSE_GENERATION, input_tokens=50, output_tokens=0)

    async with _client_for(org_a) as c:
        response_a = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})
    app.dependency_overrides.clear()

    async with _client_for(org_b) as c:
        response_b = await c.post("/v1/query", json={"question": "What was Apple's revenue?"})
    app.dependency_overrides.clear()

    assert response_a.status_code == 429
    assert response_b.status_code == 200


# --- recording happens per call (integration through generate_answer) --------


async def test_generate_answer_records_usage_per_call(monkeypatch, budget_orgs):
    """generate_answer -> _call_llm records exactly one usage write per LLM call,
    tagged with the right purpose, using the real token_usage.record_usage
    against the test DB (not mocked, unlike test_generate.py/test_generation.py
    which stub it out to isolate their own concerns)."""
    from app.generation import entailment as entailment_module
    from app.generation import generate as generate_module
    from app.generation.generate import generate_answer
    from app.models import RetrievedChunk

    org_a, _ = budget_orgs

    class _Block:
        def __init__(self, text):
            self.text = text

    class _Usage:
        def __init__(self, input_tokens, output_tokens):
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens

    class _Msg:
        def __init__(self, text, input_tokens=42, output_tokens=7):
            self.content = [_Block(text)]
            self.usage = _Usage(input_tokens, output_tokens)

    valid = (
        '{"claims": [{"text": "Apple\'s FY2024 net sales were $391,035 million.", '
        '"citations": [{"chunk_id": 514, "document_id": 61, "page": 54, '
        '"quote": "Total net sales $391,035"}]}]}'
    )
    chunk = RetrievedChunk(
        chunk_id=514, document_id=61, page=54,
        text="Total net sales $391,035 million for fiscal year 2024.",
        context_summary="Apple Inc. 10-K fiscal 2024 results", score=0.99,
    )

    from unittest.mock import MagicMock

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_Msg(valid))
    monkeypatch.setattr(generate_module, "AsyncAnthropic", lambda **kw: client)

    judge = MagicMock()
    judge.messages.create = AsyncMock(
        return_value=_Msg('{"supported": true, "reason": "evidence states it"}')
    )
    monkeypatch.setattr(entailment_module, "AsyncAnthropic", lambda **kw: judge)

    await generate_answer("What was Apple's revenue?", [chunk], org_id=org_a)

    assert await usage_today(org_a) == 49  # 42 input + 7 output, one call recorded
