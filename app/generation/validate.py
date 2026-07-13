"""Citation validator — the hallucination gate.

An answer passes only if every claim's citations (a) reference chunk_ids that
were actually retrieved, and (b) contain quotes that appear verbatim in those
chunks. Anything else is blocked before reaching the user.
"""

import re

from app.models import Answer, RetrievedChunk


class CitationValidationError(Exception):
    pass


# Rejoins a currency/open-paren symbol with the digits it belongs to. pypdf emits
# table cells on separate lines, so a filing rendering "$391,035" arrives as
# "$\n391,035" and the model's natural "$391,035" then fails the gate
# (INCIDENTS.md). Scoped deliberately to symbol->digit: it must NEVER fold
# whitespace between two digit groups, because in a financial table that
# whitespace is the only thing separating one cell from the next. Fusing them
# would let a model splice "391,035" with an adjacent footnote "2" into the
# fabricated figure "391,0352" and have the gate call it verbatim.
_SYMBOL_DIGIT_SPLIT = re.compile(r"([$(])\s+(?=\d)")


def _normalize(text: str) -> str:
    """Case- and whitespace-run-insensitive, but whitespace-preserving between
    tokens: cell boundaries in a flattened PDF table are load-bearing, not noise.
    """
    collapsed = " ".join(text.split()).lower()
    return _SYMBOL_DIGIT_SPLIT.sub(r"\1", collapsed)


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
