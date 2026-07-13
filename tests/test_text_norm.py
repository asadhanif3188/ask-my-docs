"""app.text_norm canonicalises pypdf's extraction artifacts, and is shared by
ingestion and the citation validator.

Every case below is an artifact found by auditing the real 12-filing corpus, not
one imagined. Each would otherwise make the gate reject a faithful quote, because
the model types ASCII where the filing carries a typographic glyph.

The one invariant: fold whitespace around binding symbols ($, €, parens), NEVER
between two digit groups — that whitespace is the only cell delimiter left in a
flattened financial table, and fusing digits lets a model fabricate figures the
gate would then certify as verbatim (INCIDENTS.md).
"""

import pytest

from app.text_norm import clean_pdf_text


@pytest.mark.parametrize(
    ("extracted", "expected", "why"),
    [
        ("Total net sales\n$\n391,035", "Total net sales\n$391,035",
         "currency stranded from its digits (the original incident)"),
        ("$\n(\n4,638\n)", "$(4,638)",
         "parenthesised negative — ubiquitous in 10-Ks"),
        ("€\n17,000", "€17,000",
         "same bug, different currency"),
        ("management’s assessment", "management's assessment",
         "curly apostrophe x2290 — the model types ASCII"),
        ("the “Company” and", 'the "Company" and',
         "curly double quotes x1376"),
        ("fiscal 2023—2024", "fiscal 2023-2024",
         "em dash x526 — the model types a hyphen"),
        ("FORM 10-K\n■\nANNUAL REPORT", "FORM 10-K\n \nANNUAL REPORT",
         "checkbox glyph x248 — the model omits it"),
        ("among other things:\n\x7f\nThe introduction", "among other things:\n \nThe introduction",
         "bullets extracted as control chars x762"),
        # Already-clean text must survive untouched.
        ("Total net sales $391,035", "Total net sales $391,035", "no-op on clean text"),
    ],
)
def test_folds_every_audited_artifact(extracted, expected, why):
    assert clean_pdf_text(extracted) == expected, why


def test_model_ascii_and_filing_glyphs_converge():
    """The point of canonicalising BOTH sides: the filing's typography and the
    model's ASCII must land on the same string, so either validates."""
    from_filing = "the Company’s “Total net sales”\n$\n391,035—up"
    from_model = 'the Company\'s "Total net sales" $391,035-up'
    assert " ".join(clean_pdf_text(from_filing).split()) == (
        " ".join(clean_pdf_text(from_model).split())
    )


def test_control_characters_become_space_not_nothing():
    """Deleting them could fuse the tokens either side — the exact mistake this
    module exists to avoid. A space keeps figures apart."""
    assert clean_pdf_text("391,035\x7f2") == "391,035 2"
    assert clean_pdf_text("391,035■2") == "391,035 2"


@pytest.mark.parametrize(
    "table_row",
    [
        "391,035\n2\n%",          # sales figure | footnote marker | percent column
        "1,234\n5,678",           # two adjacent cells
        "391,035\n383,285",       # this year | last year
        "12\n2024",               # day | year
    ],
)
def test_never_fuses_two_digit_groups(table_row):
    """The security invariant. Whatever else changes, digits stay separated —
    otherwise "391,035" + footnote "2" becomes the fabricated figure "391,0352"."""
    assert clean_pdf_text(table_row) == table_row, (
        "the cell boundary between two digit groups must survive untouched"
    )


def test_dash_folding_does_not_fuse_digits():
    """Dashes fold to a hyphen, which still separates a range — it must not become
    a splice point for two unrelated figures."""
    assert clean_pdf_text("2023—2024") == "2023-2024"
    assert "20232024" not in clean_pdf_text("2023—2024")
