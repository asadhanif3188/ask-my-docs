"""Observability hooks — deliberately thin until Project 3.

Every pipeline stage calls span() so that wiring Langfuse/OTel later is a
one-file change, not a refactor. For now: structured stdout logging.
"""

import json
import time
from contextlib import contextmanager


@contextmanager
def span(name: str, **attrs):
    start = time.perf_counter()
    error: str | None = None
    try:
        yield
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        print(json.dumps({
            "span": name,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": error,
            **attrs,
        }))
