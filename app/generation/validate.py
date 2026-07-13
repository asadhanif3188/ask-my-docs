"""Citation validator — the hallucination gate.

An answer passes only if every claim's citations (a) reference chunk_ids that
were actually retrieved, and (b) contain quotes that appear verbatim in those
chunks. Anything else is blocked before reaching the user.
"""

from app.models import Answer, RetrievedChunk
from app.text_norm import rejoin_symbol_digits


class CitationValidationError(Exception):
    pass


def _normalize(text: str) -> str:
    """Case- and whitespace-run-insensitive, but whitespace-PRESERVING between
    tokens: cell boundaries in a flattened PDF table are load-bearing, not noise.

    Applies the same symbol/digit rejoining as ingestion (app.text_norm) so a
    quote still validates against chunks stored before that repair existed —
    and so the two can never drift into disagreeing about what a chunk says.
    """
    collapsed = " ".join(text.split()).lower()
    return rejoin_symbol_digits(collapsed)


def validate_answer(answer: Answer, retrieved: list[RetrievedChunk]) -> None:
    chunks_by_id = {c.chunk_id: c for c in retrieved}

    for claim in answer.claims:
        if not claim.citations:
            raise CitationValidationError(f"Uncited claim: {claim.text[:80]!r}")
        for citation in claim.citations:
            chunk = chunks_by_id.get(citation.chunk_id)
            if chunk is None:
                raise CitationValidationError(
                    f"Citation references chunk_id={citation.chunk_id} that was not retrieved"
                )
            if _normalize(citation.quote) not in _normalize(chunk.text):
                raise CitationValidationError(
                    f"Quote not found verbatim in chunk {citation.chunk_id}: "
                    f"{citation.quote[:80]!r}"
                )
