"""Per-org token spend recording and budget lookups.

Grain is (org_id, day, purpose) — see migrations/002_token_accounting.sql.
Every successful LLM call (generation, repair, ingestion summary) records its
input/output split here via record_usage(); the budget check in api/query.py
calls usage_today()/daily_limit() to sum across purposes for today (UTC).
"""

import datetime

from app.config import get_settings
from app.db import get_pool

PURPOSE_GENERATION = "generation"
PURPOSE_REPAIR = "repair"
PURPOSE_SUMMARY = "summary"


def _today_utc() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


async def record_usage(*, org_id: int, purpose: str, input_tokens: int, output_tokens: int) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO token_usage (org_id, day, purpose, input_tokens, output_tokens)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (org_id, day, purpose) DO UPDATE SET
            input_tokens = token_usage.input_tokens + EXCLUDED.input_tokens,
            output_tokens = token_usage.output_tokens + EXCLUDED.output_tokens
        """,
        org_id, _today_utc(), purpose, input_tokens, output_tokens,
    )


async def usage_today(org_id: int) -> int:
    """Total input+output tokens spent by org_id today (UTC), across all purposes."""
    pool = await get_pool()
    total = await pool.fetchval(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) "
        "FROM token_usage WHERE org_id = $1 AND day = $2",
        org_id, _today_utc(),
    )
    return int(total)


async def daily_limit(org_id: int) -> int:
    """Per-org override from orgs.daily_token_budget, falling back to the
    app-wide default for an org row that (unexpectedly) doesn't exist."""
    pool = await get_pool()
    configured = await pool.fetchval("SELECT daily_token_budget FROM orgs WHERE id = $1", org_id)
    return int(configured) if configured is not None else get_settings().org_daily_token_budget
