"""The second faithfulness layer: does the claim SENTENCE match what it cites?

validate.py catches invented figures deterministically. This catches the
misstatements that invent no number — a reversed direction, a swapped unit —
which is the residue of the "quote checks out but the sentence lies" gap.

Fully mocked: no API key needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation import entailment
from app.generation.entailment import ClaimNotEntailedError, verify_entailment
from app.models import Answer, Citation, Claim, RetrievedChunk

CHUNK = RetrievedChunk(
    chunk_id=1, document_id=10, page=14,
    text="Total revenue for fiscal 2024 was $4.2 billion, up 12% year over year.",
    context_summary="ACME Corp 10-K, fiscal 2024", score=0.9,
)


def _answer(claim_text: str) -> Answer:
    return Answer(claims=[Claim(
        text=claim_text,
        citations=[Citation(chunk_id=1, document_id=10, page=14,
                            quote="Total revenue for fiscal 2024 was $4.2 billion")],
    )])


class _Block:
    def __init__(self, text: str):
        self.text = text


class _Msg:
    def __init__(self, text: str):
        self.content = [_Block(text)]


def _judge_returning(payload: str) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_Msg(payload))
    return client


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_ENTAILMENT_CHECK", "true")
    yield
    get_settings.cache_clear()


async def test_faithful_claim_is_certified(monkeypatch):
    monkeypatch.setattr(
        entailment, "AsyncAnthropic",
        lambda **kw: _judge_returning('{"supported": true, "reason": "stated verbatim"}'),
    )
    await verify_entailment(_answer("Revenue for fiscal 2024 was $4.2 billion."), [CHUNK])


async def test_reversed_direction_is_blocked(monkeypatch):
    """No invented number — "up 12%" becomes "down" — so the deterministic
    numeric gate cannot see this. The judge must."""
    monkeypatch.setattr(
        entailment, "AsyncAnthropic",
        lambda **kw: _judge_returning(
            '{"supported": false, "reason": "evidence says revenue rose, claim says it fell"}'
        ),
    )
    with pytest.raises(ClaimNotEntailedError, match="not supported"):
        await verify_entailment(
            _answer("Revenue for fiscal 2024 fell 12% to $4.2 billion."), [CHUNK]
        )


async def test_swapped_unit_is_blocked(monkeypatch):
    """"$4.2 million" vs "$4.2 billion": the figure 4.2 IS grounded, so only a
    semantic check catches the three-orders-of-magnitude lie."""
    monkeypatch.setattr(
        entailment, "AsyncAnthropic",
        lambda **kw: _judge_returning('{"supported": false, "reason": "billions, not millions"}'),
    )
    with pytest.raises(ClaimNotEntailedError):
        await verify_entailment(_answer("Revenue was just $4.2 million."), [CHUNK])


async def test_check_fails_closed_when_the_judge_is_unavailable(monkeypatch):
    """An unreachable judge means the claim is UNVERIFIED. Unverified financial
    claims do not get served as fact — the API still returns the sources."""

    def dead_client(**kw):
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=RuntimeError("upstream down"))
        return client

    monkeypatch.setattr(entailment, "AsyncAnthropic", dead_client)

    with pytest.raises(ClaimNotEntailedError, match="unavailable"):
        await verify_entailment(_answer("Revenue was $4.2 billion."), [CHUNK])


async def test_malformed_judge_output_fails_closed(monkeypatch):
    monkeypatch.setattr(
        entailment, "AsyncAnthropic", lambda **kw: _judge_returning("not json at all")
    )
    with pytest.raises(ClaimNotEntailedError):
        await verify_entailment(_answer("Revenue was $4.2 billion."), [CHUNK])


async def test_check_can_be_disabled(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ENABLE_ENTAILMENT_CHECK", "false")

    def explode(**kw):
        raise AssertionError("judge must not be called when the check is disabled")

    monkeypatch.setattr(entailment, "AsyncAnthropic", explode)
    await verify_entailment(_answer("anything at all"), [CHUNK])
    get_settings.cache_clear()
