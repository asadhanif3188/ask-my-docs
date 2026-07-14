"""Query rewrite: split the raw question into a dense form and an FTS form
before hybrid_retrieve fans out (Phase 1 query path: rewrite -> hybrid
retrieve -> RRF -> rerank -> generate).

One cheap-tier LLM call, strict JSON out. Behind settings.enable_rewrite so
the on/off ablation in METRICS.md is a config flip, not a code change.

Failure contract: rewriting must never block a query. Any failure — API
down, malformed JSON, an empty field — falls back to the raw question for
both branches, logged as a warning.
"""

import json
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You rewrite a user's question into two forms for a hybrid search system.

Return ONLY JSON: {"dense": "...", "fts": "..."}

"dense" feeds a semantic embedding search: resolve pronouns and vague
references using the question's own context, expand domain abbreviations
(e.g. "R&D" -> "research and development"), and add close synonyms. It is
fine to be more verbose than the original question.

"fts" feeds an exact keyword search: strip filler words, but NEVER
paraphrase, translate, or replace exact terms. Identifiers, tickers, proper
nouns, dates, and numbers must appear byte-for-byte as in the original
question. This is the exact-match branch; altering an identifier here
defeats the reason hybrid search exists.

If there is nothing to rewrite, echo the question unchanged in both fields.
"""


@dataclass(frozen=True)
class RewrittenQuery:
    dense: str
    fts: str


@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def _call_rewrite_llm(question: str) -> str:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.llm_api_key)
    message = await client.messages.create(
        model=settings.summary_model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text


async def rewrite_query(question: str) -> RewrittenQuery:
    settings = get_settings()
    if not settings.enable_rewrite:
        return RewrittenQuery(dense=question, fts=question)

    try:
        raw = await _call_rewrite_llm(question)
        from app.generation.generate import _extract_json  # same fenced-JSON tolerance

        parsed = json.loads(_extract_json(raw))
        dense = str(parsed["dense"]).strip()
        fts = str(parsed["fts"]).strip()
        if not dense or not fts:
            raise ValueError("rewrite returned an empty dense/fts field")
    except Exception:
        logger.warning("rewrite_query failed; falling back to the raw question", exc_info=True)
        return RewrittenQuery(dense=question, fts=question)

    return RewrittenQuery(dense=dense, fts=fts)
