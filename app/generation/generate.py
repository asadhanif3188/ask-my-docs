"""LLM generation with citation-enforced JSON output.

Contract: the model must return Answer JSON where every Claim carries >= 1
Citation pointing at a provided chunk_id, with a verbatim quote. validate.py
rejects anything else. On repeated failure raise GenerationUnavailable — the
API layer still returns retrieved sources (never a 500).

A rejected answer gets exactly ONE repair round: the model is shown its own output
and the specific reason it was rejected, and asked to correct it. One, not a loop —
a model that cannot satisfy the validator on the second look does not converge on
the fifth, it just bills you four more times while the user waits.

The repair CANNOT weaken the gate: its output goes through the identical
validate_answer + verify_entailment path. The worst a bad repair can do is fail
again, which is where we already were.
"""

import json
import logging

from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.generation.validate import CitationValidationError, validate_answer
from app.models import Answer, RetrievedChunk

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You answer questions strictly from the provided source chunks.
Return ONLY JSON matching this schema:
{"claims":[{"text":"...","citations":[{"chunk_id":<int>,"document_id":<int>,"page":<int|null>,"quote":"<verbatim span from that chunk>"}]}]}
Rules:
- Every claim MUST cite at least one provided chunk_id with a verbatim quote.
- If the sources do not support an answer, return {"claims":[]}.
- Never use knowledge outside the provided chunks.
"""

REPAIR_PROMPT = """\
Your previous response was REJECTED by an automated validator. The failure:

{reason}

Correct it and return ONLY the corrected JSON, same schema, no commentary.
Rules you must satisfy:
- Every quote must appear VERBATIM in the chunk it cites — copy it character for
  character from the SOURCES above, do not retype, reformat, or tidy it.
- Cite only chunk_ids that appear in SOURCES.
- Every figure in a claim's text must appear in the chunks that claim cites. Do not
  compute, sum, or infer a number that is not written in the sources.
- If the sources genuinely do not support an answer, return {{"claims":[]}} — that is
  a valid, correct response, and far better than a claim you cannot ground.
"""

# Stable telemetry identifiers. Project 3 aggregates these, so they are set at the
# raise site (validate.py / entailment.py carry a `category`) and never parsed back
# out of an error message someone might reword.
CATEGORY_JSON_DECODE = "json_decode_error"
CATEGORY_SCHEMA_INVALID = "schema_invalid"
CATEGORY_UNKNOWN = "unknown_validation_error"
# The one failure a repair round cannot fix: our judge is down, not the model's output.
CATEGORY_ENTAILMENT_UNAVAILABLE = "entailment_unavailable"


class GenerationUnavailable(Exception):
    pass


def _failure_category(exc: Exception) -> str:
    """Stable category for telemetry. Prefers the one the raiser set."""
    category = getattr(exc, "category", None)
    if category:
        return str(category)
    if isinstance(exc, json.JSONDecodeError):
        return CATEGORY_JSON_DECODE
    if isinstance(exc, ValueError):  # pydantic ValidationError subclasses ValueError
        return CATEGORY_SCHEMA_INVALID
    return CATEGORY_UNKNOWN


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a model response that may be wrapped in prose.

    Despite SYSTEM_PROMPT saying "Return ONLY JSON", Claude wraps output in a
    ```json fence — found via a real end-to-end query (INCIDENTS.md). A model
    that ignores that instruction just as easily adds "Here is the JSON:" before
    or "Let me know if..." after, so we scan for the first balanced {...} object
    rather than anchoring on the fence markers themselves. Returns text unchanged
    when no object is found, letting the caller's JSONDecodeError surface as-is.
    """
    start = text.find("{")
    if start == -1:
        return text
    decoder = json.JSONDecoder()
    try:
        _, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        return text
    return text[start:end]


def _format_sources(chunks: list[RetrievedChunk]) -> str:
    # context_summary is LLM-generated from untrusted document text (see
    # app/ingestion/chunking.summarize_document) and is itself untrusted content
    # flowing into this prompt — same trust boundary as c.text, mitigated the
    # same way: the citation validator requires verbatim quotes from c.text.
    return "\n\n".join(
        f"[chunk_id={c.chunk_id} document_id={c.document_id} page={c.page}]\n"
        f"{c.context_summary}\n{c.text}"
        for c in chunks
    )


@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def _call_llm(messages: list[dict]) -> str:
    """The single path to the model. Both the first attempt and the repair go through
    it, so the repair inherits this tenacity policy instead of nesting a second one
    inside it — retries around retries turn one 3-attempt budget into nine."""
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.llm_api_key)
    message = await client.messages.create(
        model=settings.llm_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    # TODO(phase2): record input/output tokens into token_usage for the org budget cutoff
    return message.content[0].text


def _first_turn(question: str, chunks: list[RetrievedChunk]) -> dict:
    return {
        "role": "user",
        "content": f"SOURCES:\n{_format_sources(chunks)}\n\nQUESTION: {question}",
    }


async def _parse_and_validate(
    raw: str, chunks: list[RetrievedChunk], degraded: bool
) -> Answer:
    """Every gate, in order: schema -> citations -> numbers -> entailment.

    The repair round calls this too. That is the point: a repaired answer is held to
    the identical standard, so the repair can only ever turn a rejection into a
    *validated* answer, never into a waved-through one.
    """
    answer = Answer(**json.loads(_extract_json(raw)), degraded=degraded)
    validate_answer(answer, chunks)
    # Deterministic gates passed; now the semantic one. Imported here to keep the
    # module import cycle-free (entailment imports CitationValidationError).
    from app.generation.entailment import verify_entailment

    await verify_entailment(answer, chunks)
    return answer


async def generate_answer(
    question: str, chunks: list[RetrievedChunk], degraded: bool = False
) -> Answer:
    first_turn = _first_turn(question, chunks)

    try:
        raw = await _call_llm([first_turn])
    except Exception as exc:
        raise GenerationUnavailable(str(exc)) from exc

    try:
        return await _parse_and_validate(raw, chunks, degraded)
    except (json.JSONDecodeError, CitationValidationError, ValueError) as exc:
        category = _failure_category(exc)
        logger.warning("generation rejected [%s]: %s", category, exc)

        if category == CATEGORY_ENTAILMENT_UNAVAILABLE:
            # Our judge is down; the model's output may well be fine. Asking it to
            # "repair" an answer we never managed to check would spend a second call
            # to hit the identical dead judge. Fail closed now, at half the price.
            raise GenerationUnavailable(f"Output failed citation validation: {exc}") from exc

        first_failure = exc

    # Exactly one repair round. Not a loop — see module docstring.
    try:
        repaired_raw = await _call_llm([
            first_turn,
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REPAIR_PROMPT.format(reason=str(first_failure))},
        ])
    except Exception as exc:
        raise GenerationUnavailable(str(exc)) from exc

    try:
        answer = await _parse_and_validate(repaired_raw, chunks, degraded)
    except (json.JSONDecodeError, CitationValidationError, ValueError) as exc:
        repair_category = _failure_category(exc)
        logger.warning(
            "repair round also rejected [%s -> %s]: %s",
            _failure_category(first_failure), repair_category, exc,
        )
        raise GenerationUnavailable(f"Output failed citation validation: {exc}") from exc

    logger.warning(
        "generation repaired after [%s] rejection", _failure_category(first_failure)
    )
    return answer
