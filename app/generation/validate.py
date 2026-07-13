"""Citation validator — the hallucination gate.

An answer passes only if, for every claim:
  (a) its citations reference chunk_ids that were actually retrieved,
  (b) each quote appears verbatim in the cited chunk, and
  (c) every significant figure asserted in the claim *sentence* appears in the
      cited chunks.

(c) exists because (a) and (b) alone check the *quote*, not the sentence the user
reads. A model could attach a flawless, correctly-attributed quote to a sentence
that misrepresented it — "Revenue COLLAPSED by 90%..." citing a verbatim
"Total revenue for fiscal 2024 was $4.2 billion" — and the gate would pass it.
For a financial corpus the invented *number* is the catastrophic failure, so
numbers get a hard, deterministic check here. Misstatements that carry no new
figure (a reversed direction, a wrong unit) are caught by the entailment pass in
generation/entailment.py, which is probabilistic; this is not.
"""

import re

from app.models import Answer, RetrievedChunk
from app.text_norm import clean_pdf_text


class CitationValidationError(Exception):
    pass


_NUMBER = re.compile(r"\d[\d,.]*")


def _significant_numbers(text: str) -> set[str]:
    """Figures worth grounding: anything but a bare single digit.

    Single digits are excluded deliberately. They appear all over a filing
    (footnote markers, list indices), so demanding a match would reject correct
    answers — the very bug this module already has an incident for — while
    barely constraining a fabricator. Two digits up ("90%", "41%", "$4.2
    billion", "391,035") is where invented figures actually live.
    """
    found = set()
    for match in _NUMBER.finditer(text):
        token = match.group(0).rstrip(".,")
        if len(token) >= 2:
            found.add(token)
    return found


def _is_grounded(number: str, evidence: str) -> bool:
    return number in evidence or number.replace(",", "") in evidence


def _normalize(text: str) -> str:
    """Case- and whitespace-run-insensitive, but whitespace-PRESERVING between
    tokens: cell boundaries in a flattened PDF table are load-bearing, not noise.

    Applies the same artifact cleaning as ingestion (app.text_norm) so a quote
    still validates against chunks stored before that repair existed — and so the
    two can never drift into disagreeing about what a chunk says.

    Order matters, and getting it backwards was a live bug: cleaning turns a
    control character into a space, so collapsing first leaves a fresh run of
    spaces behind that nothing re-collapses, and the quote stops matching. Clean,
    then collapse — the same order ingestion uses.
    """
    return " ".join(clean_pdf_text(text).split()).lower()


def validate_answer(answer: Answer, retrieved: list[RetrievedChunk]) -> None:
    chunks_by_id = {c.chunk_id: c for c in retrieved}

    for claim in answer.claims:
        if not claim.citations:
            raise CitationValidationError(f"Uncited claim: {claim.text[:80]!r}")

        cited_chunks = []
        for citation in claim.citations:
            chunk = chunks_by_id.get(citation.chunk_id)
            if chunk is None:
                raise CitationValidationError(
                    f"Citation references chunk_id={citation.chunk_id} that was not retrieved"
                )
            if _normalize(citation.quote) not in _normalize(chunk.text):
                # Report the whole quote, not a prefix. An 80-char truncation hid
                # the actual point of divergence (a \x7f bullet glyph 75 chars in)
                # and cost a manual reproduction to find — see INCIDENTS.md.
                raise CitationValidationError(
                    f"Quote not found verbatim in chunk {citation.chunk_id}: "
                    f"{citation.quote!r}"
                )
            cited_chunks.append(chunk)

        # The sentence the user reads must not assert figures its own sources
        # never mention. Grounded against the whole cited chunk plus its context
        # summary, not just the quoted span: a claim legitimately draws the
        # fiscal year or a comparative from around the quote — a table cell
        # rarely repeats the year its own document is about.
        evidence = _normalize(
            " ".join(f"{c.context_summary} {c.text}" for c in cited_chunks)
        )
        for number in _significant_numbers(claim.text):
            if not _is_grounded(_normalize(number), evidence):
                raise CitationValidationError(
                    f"Claim asserts {number!r}, which is not supported by its cited "
                    f"chunks: {claim.text[:80]!r}"
                )
