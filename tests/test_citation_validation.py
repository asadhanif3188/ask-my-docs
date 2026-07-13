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


# --- PDF extraction artifacts (INCIDENTS.md: citation gate false-rejections) ---
#
# pypdf flattens tables, emitting each cell on its own line: a filing rendering
# "$391,035" arrives as "$\n391,035". The model quotes it the natural way and a
# whitespace-sensitive gate rejects a correct, correctly-cited answer.
#
# The fix folds whitespace between a currency symbol and its digits, and NOTHING
# else. The tests below exist to hold that line: in this chunk the newlines
# between "391,035", the footnote marker "2", the "%" of an adjacent column, and
# "383,285" are the ONLY thing separating distinct table cells. A blanket
# whitespace strip (the first attempt at this fix) fused them, letting a model
# quote the fabricated figure "391,0352" or splice a footnote into an unrelated
# percent sign to invent a "2%" growth rate — and the gate called it verbatim.

PDF_CHUNK = RetrievedChunk(
    chunk_id=2, document_id=61, page=54,
    text="Total net sales\n$\n391,035\n2\n%\n$\n383,285",
    context_summary="Apple Inc. 10-K fiscal 2024", score=0.9,
)


def _pdf_answer(quote: str) -> Answer:
    return Answer(claims=[Claim(
        text="Apple's FY2024 net sales were $391,035 million.",
        citations=[Citation(chunk_id=2, document_id=61, page=54, quote=quote)],
    )])


def test_quote_validates_when_pdf_split_the_dollar_sign_onto_its_own_line():
    # The real regression: model writes "$391,035", chunk holds "$\n391,035".
    validate_answer(_pdf_answer("Total net sales $391,035"), [PDF_CHUNK])


def test_quote_validates_when_model_reproduces_the_raw_pdf_whitespace():
    # The other coin-flip: the model mirrors the extraction artifact instead.
    validate_answer(_pdf_answer("Total net sales\n$\n391,035"), [PDF_CHUNK])


def test_quote_validates_against_a_chunk_still_holding_a_bullet_control_char():
    """Chunks ingested before the artifact repair still carry \\x7f. The validator
    must clean BEFORE collapsing whitespace — doing it the other way round turns
    the control char into a space that nothing re-collapses, and a faithful quote
    stops matching. That ordering bug was live; this pins it.
    """
    chunk = RetrievedChunk(
        chunk_id=6, document_id=64, page=20,
        text="may occur from, among other things:\n\x7f\nThe introduction of new features",
        context_summary="Microsoft 10-K", score=0.9,
    )
    answer = Answer(claims=[Claim(
        text="Reputational damage may follow the introduction of new features.",
        citations=[Citation(chunk_id=6, document_id=64, page=20,
                            quote="among other things: The introduction of new features")],
    )])
    validate_answer(answer, [chunk])


def test_quote_validates_for_a_parenthesised_negative():
    """10-Ks render losses/outflows as "$(4,638)"; pypdf splits it into
    "$\n(\n4,638\n)". The model quotes what the filing shows."""
    chunk = RetrievedChunk(
        chunk_id=3, document_id=61, page=30,
        text="Net loss\n$\n(\n4,638\n)\nfor the period",
        context_summary="Apple Inc. 10-K", score=0.9,
    )
    answer = Answer(claims=[Claim(
        text="The company reported a net loss of $(4,638).",
        citations=[Citation(chunk_id=3, document_id=61, page=30, quote="Net loss $(4,638)")],
    )])
    validate_answer(answer, [chunk])


# --- Claim-text grounding -----------------------------------------------------
#
# The gate used to check only the *quote*. A model could therefore attach a
# perfectly verbatim, correctly-attributed quote to a claim sentence that
# misrepresented it — and the sentence is what the user actually reads. Every
# significant figure asserted in claim.text must now appear in the cited chunks.

REVENUE_CHUNK = RetrievedChunk(
    chunk_id=4, document_id=61, page=14,
    text="Total revenue for fiscal 2024 was $4.2 billion, up 12% year over year.",
    context_summary="ACME Corp 10-K", score=0.9,
)


def _claim_citing_revenue(claim_text: str) -> Answer:
    return Answer(claims=[Claim(
        text=claim_text,
        citations=[Citation(chunk_id=4, document_id=61, page=14,
                            quote="Total revenue for fiscal 2024 was $4.2 billion")],
    )])


def test_claim_inventing_a_figure_is_blocked_despite_a_verbatim_quote():
    # The exact hole found in review: the quote checks out perfectly, the
    # sentence the user reads does not. "90%" appears nowhere in the source.
    with pytest.raises(CitationValidationError, match="not supported"):
        validate_answer(
            _claim_citing_revenue("Revenue COLLAPSED by 90% to just $4.2 billion."),
            [REVENUE_CHUNK],
        )


def test_faithful_claim_still_passes():
    validate_answer(
        _claim_citing_revenue("Total revenue for fiscal 2024 was $4.2 billion, up 12%."),
        [REVENUE_CHUNK],
    )


@pytest.mark.parametrize(
    "real_claim",
    [
        # Claims actually produced by the live pipeline — these must not regress.
        "Apple's total net sales for fiscal year 2024 were $391,035 million.",
        "Apple's total net revenue for fiscal year 2024 was $391,035 million.",
    ],
)
def test_real_pipeline_claims_are_not_false_rejected(real_claim):
    """Grounding must not repeat the false-rejection bug it sits next to: figures
    the model legitimately restates from the chunk have to keep passing."""
    chunk = RetrievedChunk(
        chunk_id=5, document_id=61, page=54,
        text="Total net sales\n$\n391,035\n2\n%\nnet sales by category for 2024, 2023 and 2022 "
             "(dollars in millions)",
        context_summary="Apple Inc. 10-K fiscal 2024", score=0.9,
    )
    answer = Answer(claims=[Claim(
        text=real_claim,
        citations=[Citation(chunk_id=5, document_id=61, page=54,
                            quote="Total net sales $391,035")],
    )])
    validate_answer(answer, [chunk])


def test_fabricated_number_not_present_in_chunk_is_blocked():
    with pytest.raises(CitationValidationError, match="not found verbatim"):
        validate_answer(_pdf_answer("Total net sales $999,999"), [PDF_CHUNK])


def test_out_of_order_splice_is_blocked():
    with pytest.raises(CitationValidationError, match="not found verbatim"):
        validate_answer(_pdf_answer("Total net sales $383,285 391,035"), [PDF_CHUNK])


# The adversarial cases: order-preserving splices across a whitespace-only cell
# boundary. Every character is present, in order — only the cell delimiter is
# gone. These MUST stay blocked, or the gate certifies invented figures.
@pytest.mark.parametrize(
    "fabricated_quote",
    [
        "391,0352",       # sales figure fused with the footnote marker
        "391,035 2%",     # footnote marker spliced into another column's "%"
        "2%",             # a growth rate that appears nowhere in the filing
        "$391,0352%",     # both splices at once
    ],
)
def test_cross_cell_splice_is_blocked(fabricated_quote):
    with pytest.raises(CitationValidationError, match="not found verbatim"):
        validate_answer(_pdf_answer(fabricated_quote), [PDF_CHUNK])
