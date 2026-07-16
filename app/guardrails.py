"""Input validation guardrails for POST /query.

This module provides lightweight, defense-in-depth input validation:
- Length/shape checks reject obviously malformed inputs (empty, oversized, encoded blobs)
- Prompt-injection heuristic catches classic families (instruction-override, role-play, model-directed)
- Optional topic gate enforces per-org allowlist (off by default, out of scope for MVP)

IMPORTANT: This is a cheap first-filter gate, NOT the security boundary. The real
hallucination/injection backstop runs downstream in two layers:
  1. generate.py's system prompt confines all answers to retrieved source chunks
  2. validate.py's citation validator blocks any claim not supported by chunk text

An attacker who bypasses these guardrails will still be unable to generate unsourced
answers or address the model directly — both defenses are mandatory, independent of
input validation. Guardrails are an efficiency layer: rejecting garbage early saves
compute and latency before retrieval/generation.
"""

import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)

# An unbroken run longer than the longest real English word (~45 chars) signals
# encoded/machine data (base64, hex, digit runs) rather than a natural question.
_MAX_TOKEN_LENGTH = 50


class ValidationError(Exception):
    """Raised when input validation fails. Category identifies the rejection reason."""

    def __init__(self, message: str, category: str):
        super().__init__(message)
        self.category = category


# Instruction-override patterns: attempts to override, disregard, or forget instructions
# Kept case-insensitive and obfuscation-tolerant (extra words, punctuation).
INSTRUCTION_OVERRIDE_PATTERNS = [
    r"ignore\s+(?:all\s+)?previous(?:ly)?\s+instructions",
    r"ignore\s+(?:the\s+)?instructions?\s+above",
    r"disregard\s+(?:all\s+)?(?:your\s+)?(?:system\s+)?instructions?",
    r"disregard\s+your\s+system\s+prompt",
    r"forget\s+(?:everything|all|what|that).*?(?:told|instructed|taught|shown|said)",
    r"override\s+(?:your\s+)?system\s+instructions?",
]

# Role-play jailbreak markers: attempts to make the model adopt unrestricted personas
ROLEPLAY_PATTERNS = [
    r"(?:you\s+)?are\s+now\s+(?:in\s+)?developer\s+mode",
    r"(?:you\s+)?are\s+actually\s+.*?(?:developer|unrestricted|unfiltered|supposed\s+to|meant\s+to)",
    r"pretend.*(?:developer\s+mode|no\s+(?:safety|restrictions)|jailbreak)",
    r"roleplay\s+as.*(?:no\s+(?:safety|restrictions)|developer|unrestricted)",
    r"imagine\s+(?:you\s+)?(?:have\s+)?no\s+restrictions",
    r"(?:act|behave)\s+like\s+you\s+have\s+no\s+restrictions",
]

# Model-directed attempts: directly addressing the model or its creators about rules/safety
MODELDIRECTED_PATTERNS = [
    r"(?:claude|anthropic)[',\s]*(?:i\s+know\s+)?you\s+(?:have|\'ve)\s+(?:any\s+)?safety",
    r"(?:anthropic|claude).*rules?\s+(?:are|is|bad|wrong|suck)",
    r"safety\s+(?:guidelines?|rules?|filters?).{0,30}(?:ignore|disregard|bypass|override)",
    # System prompt exfiltration attempts
    r"(?:repeat|print|reveal|show|output|what\s+(?:is|are))\s+your\s+(?:system\s+)?(?:prompt|instructions?)",
    r"\bDAN\b.*(?:mode|jailbreak|restrictions)",
]

# Compile all patterns (case-insensitive) for efficiency
ALL_PATTERNS = [
    ("instruction_override", INSTRUCTION_OVERRIDE_PATTERNS),
    ("roleplay", ROLEPLAY_PATTERNS),
    ("model_directed", MODELDIRECTED_PATTERNS),
]

COMPILED_PATTERNS = [
    (family, [re.compile(p, re.IGNORECASE) for p in patterns])
    for family, patterns in ALL_PATTERNS
]


def validate_question(question: str) -> None:
    """Validate input question. Raises ValidationError if rejected; returns None if valid.

    Args:
        question: The raw question string to validate

    Raises:
        ValidationError: If validation fails, with .category one of
            {length, shape, topic} or prompt_injection:<family> where family is
            one of {instruction_override, roleplay, model_directed}.
    """
    settings = get_settings()

    # 1. Length/shape validation: empty, whitespace-only, oversized, mostly non-text
    if not question or not question.strip():
        raise ValidationError("Question cannot be empty or whitespace-only", "length")

    if len(question) > settings.max_question_length:
        raise ValidationError(
            f"Question exceeds maximum length of {settings.max_question_length}",
            "length",
        )

    # Reject mostly non-text: base64-like, hex-like, or numeric blobs
    if _is_mostly_nontext(question):
        raise ValidationError("Question appears to be non-text data", "shape")

    # 2. Prompt-injection heuristic: pattern matching across all families. The
    # category carries the specific family (e.g. prompt_injection:model_directed)
    # so Project 3 dashboards can break down attack types, not just their total.
    for family, compiled_regexes in COMPILED_PATTERNS:
        for pattern in compiled_regexes:
            if pattern.search(question):
                raise ValidationError(
                    "Question could not be processed",
                    f"prompt_injection:{family}",
                )

    # 3. Topic gate: optional per-org allowlist. Real topic classification is out
    # of scope for the MVP (it needs a classifier, not a keyword match — a naive
    # substring check both misses paraphrases and false-positives on legitimate
    # questions), so this only enforces a coarse keyword hint when an org opts in.
    if settings.allowed_topics and not _matches_allowed_topics(
        question, settings.allowed_topics
    ):
        raise ValidationError("Question topic not allowed", "topic")


def _matches_allowed_topics(question: str, allowed_topics: list[str]) -> bool:
    """Coarse keyword gate: True if any allowed topic appears in the question.

    Deliberately permissive (OR over keywords, case-insensitive substring) to
    minimize false negatives on legitimate questions — see the caller's note on
    why real topic classification is out of scope.
    """
    lowered = question.lower()
    return any(topic.lower() in lowered for topic in allowed_topics)


def _is_mostly_nontext(text: str) -> bool:
    """True if the input contains an unbroken run longer than any real word.

    Natural-language questions break into whitespace-separated tokens; encoded
    blobs (base64, hex dumps) and giant number runs do not. The longest words in
    English run to ~45 characters, so a token past _MAX_TOKEN_LENGTH is the
    signature of machine data, not a question.

    This is deliberately the only shape rule we keep: one threshold, explainable
    in a sentence, and backstopped downstream (a garbage query that slips through
    simply retrieves nothing). We do NOT try to classify content beyond "is this
    plausibly prose" — that would need a model and would risk false-positives on
    legitimate users, which the golden-set test exists to prevent.
    """
    return any(len(token) > _MAX_TOKEN_LENGTH for token in text.split())
