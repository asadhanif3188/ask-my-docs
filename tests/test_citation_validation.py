"""The hallucination gate must block: uncited claims, citations to
non-retrieved chunks, and quotes that are not verbatim in the cited chunk."""

import pytest

from app.generation.validate import CitationValidationError, validate_answer
from app.models import Answer, Citation, Claim, RetrievedChunk

CHUNK = RetrievedChunk(
    chunk_id=1, document_id=10, page=14,
    text="Total revenue for fiscal 2024 was $4.2 billion, up 12% year over year.",
    context_summary="ACME Corp 10-K, fiscal 2024 results", score=0.9,
)


def _answer(quote: str, chunk_id: int = 1) -> Answer:
    return Answer(claims=[Claim(
        text="Revenue was $4.2B.",
        citations=[Citation(chunk_id=chunk_id, document_id=10, page=14, quote=quote)],
    )])


def test_valid_verbatim_citation_passes():
    validate_answer(_answer("Total revenue for fiscal 2024 was $4.2 billion"), [CHUNK])


def test_citation_to_unretrieved_chunk_is_blocked():
    with pytest.raises(CitationValidationError, match="not retrieved"):
        validate_answer(_answer("Total revenue", chunk_id=999), [CHUNK])


def test_fabricated_quote_is_blocked():
    with pytest.raises(CitationValidationError, match="not found verbatim"):
        validate_answer(_answer("Revenue was five billion dollars"), [CHUNK])


def test_uncited_claim_is_blocked_at_schema_level():
    with pytest.raises(ValueError):
        Claim(text="Uncited assertion.", citations=[])


def test_quote_matching_is_whitespace_and_case_insensitive():
    validate_answer(_answer("total  revenue for FISCAL 2024 was $4.2 billion"), [CHUNK])
