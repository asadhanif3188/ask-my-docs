"""Repair of pypdf's table-flattening artifacts. Shared, deliberately, by
ingestion (which stores chunk text) and the citation validator (which compares a
quote against it) — if those two ever drifted apart the gate would start
rejecting correct answers again, which is the bug this exists to fix
(INCIDENTS.md: "citation gate false-rejects correct answers").

pypdf emits each table cell on its own line, so a filing rendering "$391,035"
arrives as "$\\n391,035", and "$(4,638)" — a parenthesised negative, ubiquitous
in 10-Ks — arrives as "$\\n(4,638\\n)". The model quotes these the way the filing
renders them, not the way pypdf mangled them.

THE INVARIANT, which is not negotiable: whitespace may be folded only where it
sits between a *binding symbol* ($, parens) and the number that symbol decorates.
It must NEVER be folded between two digit groups. In a flattened financial table
that whitespace is the only surviving cell delimiter, and fusing digits let a
model splice "391,035" with an adjacent footnote "2" into the fabricated figure
"391,0352" — which the citation gate then certified as verbatim. Every rule below
is therefore anchored on a non-digit symbol.
"""

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"([$(])\s+(?=\d)"), r"\1"),   # "$ 391,035" / "( 1,333" -> "$391,035" / "(1,333"
    (re.compile(r"(\$)\s+(?=\()"), r"\1"),     # "$ (4,638"              -> "$(4,638"
    (re.compile(r"(?<=\d)\s+(?=\))"), ""),     # "4,638 )"               -> "4,638)"
)


def rejoin_symbol_digits(text: str) -> str:
    """Fold whitespace between a currency/paren symbol and its number. Never
    between two digit groups — see the module docstring."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
