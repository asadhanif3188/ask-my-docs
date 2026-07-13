"""Canonicalisation of pypdf's extraction artifacts. Shared, deliberately, by
ingestion (which stores chunk text) and the citation validator (which compares a
quote against it) — if those two ever drifted apart, the gate would start
rejecting correct answers, which is the entire bug family this exists to kill
(INCIDENTS.md: "citation gate false-rejects correct answers").

Because it runs on BOTH sides of the comparison, this is a canonicalisation, not
a repair: the filing's curly "management’s" and the model's ASCII "management's"
both fold to the same string, so either rendering validates. That is why fixing
these does NOT require re-ingesting the corpus — a quote validates against a
chunk written by any pipeline version.

The artifacts, all found by auditing the real 12-filing corpus rather than
guessing (counts from that audit):

  \\x7f  x762    symbol-font bullets, extracted as control characters
  ’ ‘   x2290   curly quotes; the model types the ASCII '
  “ ”   x1376   curly doubles; the model types the ASCII "
  — –   x526    dashes; the model types a hyphen
  ■     x248    checkbox/bullet glyph; the model omits it entirely
  $ € ( x—      currency/paren stranded from the digits they bind to

THE INVARIANT, which is not negotiable: whitespace may be folded only between a
*binding symbol* ($, €, parens) and the number it decorates. NEVER between two
digit groups. In a flattened financial table that whitespace is the only surviving
cell delimiter, and fusing digits let a model splice "391,035" with an adjacent
footnote "2" into the fabricated figure "391,0352" — which the gate then certified
as verbatim. Every rule below is anchored on a non-digit symbol for that reason.
"""

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Glyph noise the model never reproduces -> a SPACE, never nothing. Deleting
    # would fuse the tokens either side, the one mistake this module cannot make.
    (re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f■▪●•◦]"), " "),
    # Punctuation the model retypes in ASCII. Folding both sides makes them agree.
    (re.compile(r"[‘’‚‛]"), "'"),
    (re.compile(r"[“”„‟]"), '"'),
    (re.compile(r"[‒–—―]"), "-"),
    # A currency/open-paren symbol rejoins the number (or nil dash) it binds to.
    (re.compile(r"([$€£¥(])\s+(?=[\d-])"), r"\1"),   # "$ 391,035" -> "$391,035"
    (re.compile(r"([$€£¥])\s+(?=\()"), r"\1"),        # "$ (4,638"  -> "$(4,638"
    (re.compile(r"(?<=\d)\s+(?=\))"), ""),            # "4,638 )"   -> "4,638)"
)


def clean_pdf_text(text: str) -> str:
    """Fold PDF extraction artifacts to a canonical form. Never folds whitespace
    between two digit groups — see the module docstring."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
