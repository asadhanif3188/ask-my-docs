"""rewrite_query(): splits a question into a dense (semantic) form and an FTS
(keyword) form before hybrid_retrieve fans out.

The one invariant that matters most: the FTS form must never have its exact
identifiers touched — that branch's whole reason to exist is exact match, and
a "helpful" paraphrase there defeats it. The other: rewriting must never block
a query, so any failure (dead API, malformed JSON, an empty field) falls back
to the raw question for both branches.

Fully mocked: no API key needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.retrieval import rewrite as rewrite_module
from app.retrieval.rewrite import RewrittenQuery, rewrite_query


class _Block:
    def __init__(self, text: str):
        self.text = text


class _Msg:
    def __init__(self, text: str):
        self.content = [_Block(text)]


def _llm_returning(payload: str) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_Msg(payload))
    return client


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_REWRITE", "true")
    yield
    get_settings.cache_clear()


async def test_identifiers_survive_verbatim_in_the_fts_form(monkeypatch):
    monkeypatch.setattr(
        rewrite_module, "AsyncAnthropic",
        lambda **kw: _llm_returning(
            '{"dense": "Apple Inc.\'s research and development spending for fiscal '
            'year 2024, ticker AAPL", "fts": "AAPL R&D spending FY2024"}'
        ),
    )

    result = await rewrite_query("What was AAPL's R&D spending FY2024?")

    assert result.fts == "AAPL R&D spending FY2024"
    assert "AAPL" in result.fts
    assert "FY2024" in result.fts
    assert result.dense != result.fts  # dense form is expanded, not identical


async def test_fallback_returns_raw_question_on_llm_error(monkeypatch, caplog):
    def dead_client(**kw):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=RuntimeError("upstream down"))
        return client

    monkeypatch.setattr(rewrite_module, "AsyncAnthropic", dead_client)

    with caplog.at_level("WARNING", logger="app.retrieval.rewrite"):
        result = await rewrite_query("What was AAPL's revenue?")

    assert result == RewrittenQuery(dense="What was AAPL's revenue?", fts="What was AAPL's revenue?")
    assert any("rewrite_query failed" in r.getMessage() for r in caplog.records)


async def test_fallback_returns_raw_question_on_malformed_json(monkeypatch):
    monkeypatch.setattr(
        rewrite_module, "AsyncAnthropic", lambda **kw: _llm_returning("not json at all")
    )

    result = await rewrite_query("What was AAPL's revenue?")

    assert result == RewrittenQuery(dense="What was AAPL's revenue?", fts="What was AAPL's revenue?")


async def test_fallback_returns_raw_question_on_empty_field(monkeypatch):
    monkeypatch.setattr(
        rewrite_module, "AsyncAnthropic",
        lambda **kw: _llm_returning('{"dense": "", "fts": "AAPL revenue"}'),
    )

    result = await rewrite_query("What was AAPL's revenue?")

    assert result == RewrittenQuery(dense="What was AAPL's revenue?", fts="What was AAPL's revenue?")


async def test_rewrite_disabled_makes_no_llm_call(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_REWRITE", "false")

    def explode(**kw):
        raise AssertionError("rewrite LLM must not be called when the flag is disabled")

    monkeypatch.setattr(rewrite_module, "AsyncAnthropic", explode)

    result = await rewrite_query("What was AAPL's revenue?")

    assert result == RewrittenQuery(dense="What was AAPL's revenue?", fts="What was AAPL's revenue?")
    get_settings.cache_clear()
