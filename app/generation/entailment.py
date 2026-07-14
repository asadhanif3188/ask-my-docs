"""Faithfulness gate: is the claim *sentence* actually supported by the evidence
it cites?

validate.py enforces the hard, deterministic half of this — the quote must be
verbatim, and the claim may not assert a figure its sources never mention. That
still leaves misstatements which invent no number at all: reversing a direction
("revenue fell" when the filing says it rose), swapping a unit (million for
billion), or overstating certainty. Those are semantic, so the check is semantic.

Honest about what this is: an LLM checking an LLM. It is a probabilistic
mitigation, not a proof, and it is the weaker of the two layers by design — which
is why the numeric gate in validate.py is deterministic and runs first. The
prompt is deliberately biased toward refusal, because a false rejection costs a
degraded answer (the API still returns sources) while a false acceptance costs a
confident lie about someone's financials.

Runs on the cheap model tier: one extra call per claim, in parallel.
"""

import asyncio
import json
import logging

from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.generation.validate import CitationValidationError
from app.models import Answer, RetrievedChunk

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You verify whether a CLAIM is fully supported by the EVIDENCE quoted from a
financial filing. You are a safety check, not an assistant.

Return ONLY JSON: {"supported": true|false, "reason": "<short>"}

Mark supported=false if the claim does ANY of the following:
- states a figure, percentage, or date not present in the evidence
- reverses or alters a direction of change (says "fell" where evidence says "rose")
- changes a unit or magnitude (millions vs billions)
- overstates certainty, or adds causation the evidence does not state
- asserts anything the evidence does not itself say

If you are unsure, answer false. Only answer true when the evidence plainly
states the claim.
"""


class ClaimNotEntailedError(CitationValidationError):
    """The claim's own cited evidence does not support it. Subclasses
    CitationValidationError so it flows through the existing generation
    fallback: the API returns sources, never an unfaithful answer."""

    def __init__(self, message: str, category: str = "claim_not_entailed"):
        super().__init__(message, category=category)


class EntailmentUnavailableError(ClaimNotEntailedError):
    """The judge itself is broken (API down, unparseable verdict) — so the claim is
    *unverified*, not *unsupported*. Still fails closed, and still subclasses
    ClaimNotEntailedError so every existing catch keeps working.

    The distinction earns its keep in generate.py: a repair round asks the model to
    fix its own output, and the model's output was never the problem here. Repairing
    a dead judge just buys a second identical failure at full price."""

    def __init__(self, message: str):
        super().__init__(message, category="entailment_unavailable")


@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def _judge(claim_text: str, evidence: str) -> tuple[bool, str]:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.llm_api_key)
    message = await client.messages.create(
        model=settings.summary_model,  # cheap tier: this runs on every claim
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"EVIDENCE:\n{evidence}\n\nCLAIM:\n{claim_text}"}],
    )
    from app.generation.generate import _extract_json  # same fenced-JSON tolerance

    verdict = json.loads(_extract_json(message.content[0].text))
    return bool(verdict.get("supported")), str(verdict.get("reason", ""))


async def verify_entailment(answer: Answer, retrieved: list[RetrievedChunk]) -> None:
    """Raise ClaimNotEntailedError if any claim is not supported by what it cites.

    A judge that is itself broken (API down, malformed JSON) must not silently
    wave claims through: the check fails closed.
    """
    if not get_settings().enable_entailment_check:
        return

    chunks_by_id = {c.chunk_id: c for c in retrieved}

    async def check(claim) -> tuple[str, bool, str]:
        evidence = "\n".join(
            chunks_by_id[cit.chunk_id].text
            for cit in claim.citations
            if cit.chunk_id in chunks_by_id
        )
        supported, reason = await _judge(claim.text, evidence)
        return claim.text, supported, reason

    try:
        results = await asyncio.gather(*(check(c) for c in answer.claims))
    except Exception as exc:
        # Fail closed. An unavailable judge means "unverified", and an unverified
        # claim about someone's financials does not get served as fact.
        logger.warning("entailment check unavailable, refusing to certify answer: %s", exc)
        raise EntailmentUnavailableError(f"entailment check unavailable: {exc}") from exc

    for claim_text, supported, reason in results:
        if not supported:
            raise ClaimNotEntailedError(
                f"Claim not supported by its cited evidence ({reason}): {claim_text[:80]!r}"
            )
