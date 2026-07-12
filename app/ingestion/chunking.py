"""Contextual chunking (Anthropic-style).

1. Split extracted text into ~500-token chunks at sentence boundaries.
2. Generate a one-line document-level summary once per document (LLM call).
3. Prepend that summary (context_summary) to each chunk before embedding —
   the raw chunk text is stored separately so quotes can still be verified verbatim.
"""

import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger(__name__)

TARGET_CHUNK_TOKENS = 500
APPROX_CHARS_PER_TOKEN = 4

# Docs can run past 200k chars; sending the whole thing to the summary model
# would be slow and defeats the point of using a cheap tier. Titles, section
# headers, and abstracts cluster in the opening pages, and totals/conclusions
# cluster at the end, so head+tail preserves document identity at a fraction
# of the token cost.
SUMMARY_HEAD_CHARS = 8_000
SUMMARY_TAIL_CHARS = 2_000
SUMMARY_MAX_OUTPUT_CHARS = 200

SUMMARY_SYSTEM_PROMPT = """\
You summarize a document in exactly one sentence (<= 200 characters) stating
what the document is and its key topics, e.g. "Acme Corp 10-K annual filing
for fiscal year 2024, covering revenue, risk factors, and segment results."
Return only that sentence, no preamble, no markdown, no line breaks.
"""


@dataclass
class Chunk:
    index: int
    page: int | None
    text: str
    context_summary: str = ""


def split_into_chunks(pages: list[tuple[int, str]]) -> list[Chunk]:
    """pages: list of (page_number, text). Splits at sentence boundaries near the
    target size; never splits mid-sentence."""
    max_chars = TARGET_CHUNK_TOKENS * APPROX_CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    for page_no, text in pages:
        sentences = _split_sentences(text)
        current: list[str] = []
        length = 0
        for sentence in sentences:
            if length + len(sentence) > max_chars and current:
                chunks.append(Chunk(index=len(chunks), page=page_no, text=" ".join(current)))
                current, length = [], 0
            current.append(sentence)
            length += len(sentence)
        if current:
            chunks.append(Chunk(index=len(chunks), page=page_no, text=" ".join(current)))
    return chunks


def _split_sentences(text: str) -> list[str]:
    # Simple heuristic splitter; swap for a proper sentence tokenizer if evals
    # show boundary-related recall loss.
    import re

    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _truncate_for_summary(full_text: str) -> str:
    if len(full_text) <= SUMMARY_HEAD_CHARS + SUMMARY_TAIL_CHARS:
        return full_text
    head = full_text[:SUMMARY_HEAD_CHARS]
    tail = full_text[-SUMMARY_TAIL_CHARS:]
    return f"{head}\n...\n{tail}"


@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def _call_summary_llm(text: str) -> str:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.llm_api_key)
    message = await client.messages.create(
        model=settings.summary_model,
        max_tokens=100,
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    return message.content[0].text


async def summarize_document(full_text: str) -> str:
    """One-line document summary used as the contextual prefix for every chunk.

    A missing summary must degrade recall, never block ingestion: any failure
    (after retries) is logged and swallowed, returning "" rather than raising —
    matching the pipeline's quarantine-don't-crash philosophy.
    """
    truncated = _truncate_for_summary(full_text)
    try:
        raw = await _call_summary_llm(truncated)
    except Exception:
        logger.warning("summarize_document failed; continuing without a context summary", exc_info=True)
        return ""

    single_line = " ".join(raw.split())
    if len(single_line) > SUMMARY_MAX_OUTPUT_CHARS:
        single_line = single_line[: SUMMARY_MAX_OUTPUT_CHARS - 1].rstrip() + "…"
    return single_line
