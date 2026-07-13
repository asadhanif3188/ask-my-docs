"""app.text_norm repairs pypdf's table-flattening, and is shared by ingestion and
the citation validator. Its one invariant: fold whitespace around binding symbols
($, parens), NEVER between two digit groups — that whitespace is the only cell
delimiter left in a flattened financial table, and fusing digits lets a model
fabricate figures the gate would then certify as verbatim (INCIDENTS.md).
"""

import pytest

from app.text_norm import clean_pdf_text


@pytest.mark.parametrize(
    ("extracted", "expected"),
    [
        # The original incident: "$" stranded on its own line.
        ("Total net sales\n$\n391,035", "Total net sales\n$391,035"),
        # Parenthesised negatives — everywhere in 10-Ks (losses, outflows).
        ("$\n(\n4,638\n)", "$(4,638)"),
        ("Net loss\n(\n1,333\n)", "Net loss\n(1,333)"),
        # Already clean text is left alone.
        ("Total net sales $391,035", "Total net sales $391,035"),
        ("$(4,638)", "$(4,638)"),
    ],
)
def test_rejoins_symbols_with_their_numbers(extracted, expected):
    assert clean_pdf_text(extracted) == expected


def test_bullet_control_characters_become_a_space():
    """pypdf renders symbol-font bullets as DEL (\\x7f) — 800 of them in the real
    corpus. The model omits them when quoting a bulleted passage, so the gate
    rejected a faithful quote until they were normalized away."""
    extracted = "among other things:\n\x7f\nThe introduction of new features"
    assert "\x7f" not in clean_pdf_text(extracted)
    # Collapsed by the validator, the quote the model actually writes now matches.
    assert " ".join(clean_pdf_text(extracted).split()) == (
        "among other things: The introduction of new features"
    )


def test_control_characters_become_space_not_nothing():
    """Deleting them could fuse the tokens either side — the exact mistake this
    module exists to avoid. A space keeps figures apart."""
    assert clean_pdf_text("391,035\x7f2") == "391,035 2"


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
    """The security invariant. Whatever else changes, digits must stay separated —
    otherwise "391,035" + footnote "2" becomes the fabricated figure "391,0352"."""
    assert clean_pdf_text(table_row) == table_row, (
        "the cell boundary between two digit groups must survive untouched"
    )
