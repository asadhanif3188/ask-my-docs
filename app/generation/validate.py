"""Citation validator — the hallucination gate.

An answer passes only if every claim's citations (a) reference chunk_ids that
were actually retrieved, and (b) contain quotes that appear verbatim in those
chunks. Anything else is blocked before reaching the user.
"""

from app.models import Answer, RetrievedChunk


class CitationValidationError(Exception):
    pass


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


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
