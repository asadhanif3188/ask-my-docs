"""generate_answer(): JSON parsing must tolerate the model wrapping its response
in a markdown code fence and/or surrounding prose — found via a real end-to-end
/query call against real SEC 10-K chunks (see INCIDENTS.md), not a hypothetical.

Every LLM call generate_answer makes is mocked, and that means TWO clients, not
one: generate.py builds an AsyncAnthropic, and so does the entailment judge it
calls after validation. Patching only the first left the judge hitting the real
API — which passed locally off a live key in .env and failed in CI, where the
judge (correctly) fails closed with no key. A unit test that silently bills the
Anthropic API is not a unit test; see INCIDENTS.md.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation import entailment as entailment_module
from app.generation import generate as generate_module
from app.generation.generate import _extract_json, generate_answer
from app.models import RetrievedChunk

CHUNK = RetrievedChunk(
    chunk_id=514, document_id=61, page=54,
    text="Total net sales $391,035 million for fiscal year 2024.",
    context_summary="Apple Inc. 10-K fiscal 2024 results", score=0.99,
)

BODY = (
    '{"claims": [{"text": "Apple\'s FY2024 net sales were $391,035 million.", '
    '"citations": [{"chunk_id": 514, "document_id": 61, "page": 54, '
    '"quote": "Total net sales $391,035"}]}]}'
)

# Shapes the model actually emits (or plausibly may) despite "Return ONLY JSON".
RESPONSE_SHAPES = {
    "plain": BODY,
    "json_fence": f"```json\n{BODY}\n```",
    "bare_fence": f"```\n{BODY}\n```",
    "fence_with_trailing_prose": f"```json\n{BODY}\n```\nLet me know if you need more detail!",
    "fence_with_leading_prose": f"Here is the JSON you asked for:\n```json\n{BODY}\n```",
    "prose_both_sides": f"Sure! Here you go:\n```json\n{BODY}\n```\nHope that helps.",
    "crlf_fence": f"```json\r\n{BODY}\r\n```",
}


class _FakeContentBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeContentBlock(text)]


def _fake_client(text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_FakeMessage(text))
    return client


@pytest.mark.parametrize("shape", RESPONSE_SHAPES)
def test_extract_json_recovers_body_from_every_response_shape(shape):
    import json

    extracted = _extract_json(RESPONSE_SHAPES[shape])
    assert json.loads(extracted)["claims"][0]["citations"][0]["chunk_id"] == 514


def test_extract_json_passes_through_text_with_no_json_object():
    # No object to find: return unchanged so the caller's JSONDecodeError (and
    # the GenerationUnavailable path) surfaces exactly as it would have before.
    assert _extract_json("I cannot answer that.") == "I cannot answer that."


@pytest.fixture(autouse=True)
def _stub_entailment_judge(monkeypatch):
    """Stub the entailment judge's own client — this test is about JSON shapes.

    Without this the judge reaches the network: locally it answers (and bills),
    in CI it has no key and fails closed, so the same test passes on one machine
    and fails on the other. verify_entailment's real behaviour is covered, mocked,
    in test_entailment.py.
    """
    monkeypatch.setattr(
        entailment_module,
        "AsyncAnthropic",
        lambda **kw: _fake_client('{"supported": true, "reason": "evidence states it"}'),
    )


@pytest.mark.parametrize("shape", RESPONSE_SHAPES)
async def test_generate_answer_parses_every_response_shape(monkeypatch, shape):
    monkeypatch.setattr(
        generate_module, "AsyncAnthropic", lambda **kw: _fake_client(RESPONSE_SHAPES[shape])
    )

    answer = await generate_answer("What was Apple's revenue?", [CHUNK])

    assert len(answer.claims) == 1
    assert answer.claims[0].citations[0].chunk_id == 514


async def test_no_llm_call_escapes_the_mocks(monkeypatch):
    """Pin the bug itself: with no API key, generate_answer must still succeed.

    If a future change adds a third LLM call and forgets to mock it, this fails
    here — on any machine — instead of only in CI.
    """
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setattr(
        generate_module, "AsyncAnthropic", lambda **kw: _fake_client(RESPONSE_SHAPES["plain"])
    )

    answer = await generate_answer("What was Apple's revenue?", [CHUNK])

    assert answer.claims[0].citations[0].chunk_id == 514
