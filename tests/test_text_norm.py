"""app.text_norm repairs pypdf's table-flattening, and is shared by ingestion and
the citation validator. Its one invariant: fold whitespace around binding symbols
($, parens), NEVER between two digit groups — that whitespace is the only cell
delimiter left in a flattened financial table, and fusing digits lets a model
fabricate figures the gate would then certify as verbatim (INCIDENTS.md).
"""

import pytest

from app.text_norm import rejoin_symbol_digits


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
    assert rejoin_symbol_digits(extracted) == expected


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
    assert rejoin_symbol_digits(table_row) == table_row, (
        "the cell boundary between two digit groups must survive untouched"
    )
