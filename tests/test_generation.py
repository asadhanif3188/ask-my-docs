"""The repair round: one retry at the *semantic* layer, not the transport layer.

tenacity already retries a call that failed to complete. This covers the other
failure: the call completed fine and the model returned something the validator
rejected. The model is shown its own output plus the specific rejection reason and
gets exactly one more attempt.

Two invariants these tests exist to pin, both of which a "just add a retry" version
of this feature quietly breaks:

1. The repaired answer goes through the SAME validate + entail path. A repair can
   turn a rejection into a validated answer; it can never turn one into a
   waved-through answer. Test (b) is the one that would catch that regression: both
   outputs are invalid, and the result is still a refusal.
2. Exactly ONE repair. Never a loop. Test (b) also pins the call count at 2 — a loop
   would sail past it while still "passing" a naive assertion on the exception.

Every LLM call is mocked, and that means BOTH clients: the generator's, and the
entailment judge's, which builds its own inside entailment.py. Forgetting the second
is precisely what made the suite pass locally off a live API key and fail in CI (see
INCIDENTS.md).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation import entailment as entailment_module
from app.generation import generate as generate_module
from app.generation.generate import GenerationUnavailable, generate_answer
from app.models import RetrievedChunk

CHUNK = RetrievedChunk(
    chunk_id=514, document_id=61, page=54,
    text="Total net sales $391,035 million for fiscal year 2024.",
    context_summary="Apple Inc. 10-K fiscal 2024 results", score=0.99,
)

VALID = (
    '{"claims": [{"text": "Apple\'s FY2024 net sales were $391,035 million.", '
    '"citations": [{"chunk_id": 514, "document_id": 61, "page": 54, '
    '"quote": "Total net sales $391,035"}]}]}'
)

# Rejected by validate.py, category=non_verbatim_quote: the quote is a plausible
# paraphrase that appears nowhere in the chunk.
BAD_QUOTE = (
    '{"claims": [{"text": "Apple\'s FY2024 net sales were $391,035 million.", '
    '"citations": [{"chunk_id": 514, "document_id": 61, "page": 54, '
    '"quote": "Net sales totalled 391,035"}]}]}'
)

# Rejected by validate.py, category=ungrounded_number: the quote is real, the
# sentence invents a figure the chunk never states.
INVENTED_NUMBER = (
    '{"claims": [{"text": "Apple\'s FY2024 net sales grew 47% to $391,035 million.", '
    '"citations": [{"chunk_id": 514, "document_id": 61, "page": 54, '
    '"quote": "Total net sales $391,035"}]}]}'
)

NOT_JSON = "I'm afraid I can't help with that request."


class _Block:
    def __init__(self, text: str):
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 5):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Msg:
    def __init__(self, text: str):
        self.content = [_Block(text)]
        self.usage = _Usage()


def _generator(monkeypatch, *responses: str) -> MagicMock:
    """Patch the generator's client to return `responses` in order. Returns the shared
    mock so tests can assert the exact number of LLM calls."""
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=[_Msg(r) for r in responses])
    monkeypatch.setattr(generate_module, "AsyncAnthropic", lambda **kw: client)
    return client


@pytest.fixture(autouse=True)
def _stub_judge(monkeypatch):
    """The entailment judge certifies whatever it is given. Its own behaviour is
    covered in test_entailment.py; here it must simply not reach the network."""
    judge = MagicMock()
    judge.messages.create = AsyncMock(
        return_value=_Msg('{"supported": true, "reason": "evidence states it"}')
    )
    monkeypatch.setattr(entailment_module, "AsyncAnthropic", lambda **kw: judge)
    return judge


@pytest.fixture(autouse=True)
def _stub_token_usage(monkeypatch):
    """This suite is about the repair-round contract, not token accounting
    (covered in tests/test_token_budget.py) — stub the write so no real DB
    connection is required."""
    monkeypatch.setattr(generate_module, "record_usage", AsyncMock())


async def test_valid_first_output_is_not_repaired(monkeypatch):
    client = _generator(monkeypatch, VALID)

    answer = await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)

    assert answer.claims[0].citations[0].chunk_id == 514
    assert client.messages.create.await_count == 1, "a valid answer must not be repaired"


@pytest.mark.parametrize(
    ("bad_first", "category"),
    [
        (BAD_QUOTE, "non_verbatim_quote"),
        (INVENTED_NUMBER, "ungrounded_number"),
        (NOT_JSON, "json_decode_error"),
    ],
)
async def test_invalid_output_is_repaired_in_exactly_one_extra_call(
    monkeypatch, caplog, bad_first, category
):
    client = _generator(monkeypatch, bad_first, VALID)

    answer = await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)

    assert answer.claims[0].citations[0].chunk_id == 514
    assert client.messages.create.await_count == 2

    # The repair must actually TELL the model what was wrong — a bare "try again"
    # would still pass the call-count assertion while repairing nothing.
    repair_messages = client.messages.create.await_args_list[1].kwargs["messages"]
    assert [m["role"] for m in repair_messages] == ["user", "assistant", "user"]
    assert repair_messages[1]["content"] == bad_first, "model must see its own output"
    assert "REJECTED" in repair_messages[2]["content"]

    assert category in caplog.text, "the failure category must be logged for telemetry"


async def test_answer_is_refused_when_the_repair_also_fails(monkeypatch):
    client = _generator(monkeypatch, BAD_QUOTE, INVENTED_NUMBER)

    with pytest.raises(GenerationUnavailable):
        await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)

    # Exactly 2: one repair, then give up. A loop would keep going, and the raised
    # exception alone would not tell us the difference.
    assert client.messages.create.await_count == 2


async def test_repair_output_still_faces_the_full_gate(monkeypatch):
    """The repair is not a bypass. An invalid first answer plus an invalid repair
    must not produce an answer, even though the model 'tried twice'."""
    _generator(monkeypatch, NOT_JSON, INVENTED_NUMBER)

    with pytest.raises(GenerationUnavailable, match="citation validation"):
        await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)


async def test_a_dead_judge_is_not_repaired(monkeypatch, caplog):
    """When the entailment judge itself is broken, the model's output was never shown
    to be wrong — so there is nothing for it to repair. Spending a second generation
    call to hit the same dead judge is pure cost. Fail closed immediately."""
    client = _generator(monkeypatch, VALID, VALID)
    judge = MagicMock()
    judge.messages.create = AsyncMock(side_effect=RuntimeError("judge is down"))
    monkeypatch.setattr(entailment_module, "AsyncAnthropic", lambda **kw: judge)

    with pytest.raises(GenerationUnavailable):
        await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)

    assert client.messages.create.await_count == 1, "a dead judge must not trigger a repair"
    assert "entailment_unavailable" in caplog.text


async def test_a_transient_api_error_does_not_consume_the_repair(monkeypatch):
    """tenacity retries the transport failure inside _call_llm; that must not also
    burn the semantic repair round. One dropped connection, then a valid answer, is
    a 2-call success with the repair still unspent."""
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=[ConnectionError("reset"), _Msg(VALID)])
    monkeypatch.setattr(generate_module, "AsyncAnthropic", lambda **kw: client)

    answer = await generate_answer("What was Apple's revenue?", [CHUNK], org_id=1)

    assert answer.claims[0].citations[0].chunk_id == 514
    assert client.messages.create.await_count == 2  # 1 retried transport call + success
