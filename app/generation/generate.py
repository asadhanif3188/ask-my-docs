"""LLM generation with citation-enforced JSON output.

Contract: the model must return Answer JSON where every Claim carries >= 1
Citation pointing at a provided chunk_id, with a verbatim quote. validate.py
rejects anything else. On repeated failure raise GenerationUnavailable — the
API layer still returns retrieved sources (never a 500).
"""

import json

from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.generation.validate import CitationValidationError, validate_answer
from app.models import Answer, RetrievedChunk

SYSTEM_PROMPT = """\
You answer questions strictly from the provided source chunks.
Return ONLY JSON matching this schema:
{"claims":[{"text":"...","citations":[{"chunk_id":<int>,"document_id":<int>,"page":<int|null>,"quote":"<verbatim span from that chunk>"}]}]}
Rules:
- Every claim MUST cite at least one provided chunk_id with a verbatim quote.
- If the sources do not support an answer, return {"claims":[]}.
- Never use knowledge outside the provided chunks.
"""


class GenerationUnavailable(Exception):
    pass


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
async def _call_llm(question: str, chunks: list[RetrievedChunk]) -> str:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.llm_api_key)
    message = await client.messages.create(
        model=settings.llm_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"SOURCES:\n{_format_sources(chunks)}\n\nQUESTION: {question}"}
        ],
    )
    # TODO(phase2): record input/output tokens into token_usage for the org budget cutoff
    return message.content[0].text


async def generate_answer(
    question: str, chunks: list[RetrievedChunk], degraded: bool = False
) -> Answer:
    try:
        raw = await _call_llm(question, chunks)
    except Exception as exc:
        raise GenerationUnavailable(str(exc)) from exc

    try:
        answer = Answer(**json.loads(_extract_json(raw)), degraded=degraded)
        validate_answer(answer, chunks)
    except (json.JSONDecodeError, CitationValidationError, ValueError) as exc:
        # TODO(phase1): one repair round — feed the validation error back to the model
        raise GenerationUnavailable(f"Output failed citation validation: {exc}") from exc

    return answer
