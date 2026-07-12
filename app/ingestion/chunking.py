"""Contextual chunking (Anthropic-style).

1. Split extracted text into ~500-token chunks at sentence boundaries.
2. Generate a one-line document-level summary once per document (LLM call).
3. Prepend that summary (context_summary) to each chunk before embedding —
   the raw chunk text is stored separately so quotes can still be verified verbatim.
"""

from dataclasses import dataclass

TARGET_CHUNK_TOKENS = 500
APPROX_CHARS_PER_TOKEN = 4


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


async def summarize_document(full_text: str) -> str:
    """One-line document summary used as the contextual prefix for every chunk.
    TODO(phase1): implement via the generation LLM (cheap model tier is fine)."""
    raise NotImplementedError
