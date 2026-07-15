"""Per-user request rate limiting for POST /query.

In-memory fixed-window counter, not a Postgres table. Justification: this is
meant to be the *cheap* gate that runs before the org budget check (a real DB
aggregate, see app/token_usage.py) — round-tripping to Postgres for every
request just to increment a counter would make the "cheap" check cost as much
as the thing it's meant to shield, for no correctness benefit within a single
process (there is no `await` between reading and writing a bucket below, so
plain dict access can't race even without a lock).

Stated ceiling (10x-scale note, see README "What I'd do next at 10x scale"):
this state is per-process. Behind a load-balanced, multi-replica deployment,
each replica enforces N req/min independently, so a user's *effective* limit
becomes N * replica_count rather than N. That's fine at one replica; past
that the fix is a shared counter — a rate_limit table UPSERT-incremented on
(user_id, window_start), the same pattern token_usage.py already uses for
budget accounting, keeping the project's single-datastore argument intact.
"""

import math
import time

from app.config import get_settings

WINDOW_SECONDS = 60

# user_id -> (window_start_epoch_seconds, count_in_window)
_buckets: dict[int, tuple[int, int]] = {}


def check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds).

    retry_after_seconds is 0 when allowed is True and otherwise the number of
    seconds until the current fixed window rolls over.
    """
    limit = get_settings().rate_limit_per_minute
    now = time.time()
    window_start = int(now // WINDOW_SECONDS) * WINDOW_SECONDS

    bucket_start, count = _buckets.get(user_id, (window_start, 0))
    if bucket_start != window_start:
        bucket_start, count = window_start, 0
    count += 1
    _buckets[user_id] = (bucket_start, count)

    if count > limit:
        retry_after = max(1, math.ceil(bucket_start + WINDOW_SECONDS - now))
        return False, retry_after
    return True, 0
