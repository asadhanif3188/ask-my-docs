"""Cross-encoder reranking with bge-reranker-base (local, no per-call API cost).

Model is loaded lazily once per process. Measure the latency this stage adds —
it goes in METRICS.md (hybrid vs hybrid+rerank recall AND p95).
"""

import threading
from functools import lru_cache

from app.config import get_settings
from app.models import RetrievedChunk

# lru_cache does NOT make a slow loader thread-safe: concurrent first-callers all
# miss the cache and each builds its own model. Callers that rerank from a thread
# pool (evals/run_evals.py runs cases via asyncio.to_thread) therefore loaded N
# copies of the cross-encoder at once and killed the process with a SIGSEGV before
# a single case ran. The lock makes the load happen once; the cache makes it free
# thereafter. Uncontended acquisition costs nothing next to a forward pass.
_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import CrossEncoder

    settings = get_settings()
    return CrossEncoder(settings.reranker_model, cache_folder=settings.hf_cache_dir or None)


def _get_model():
    with _LOAD_LOCK:
        return _load_model()


def rerank(question: str, candidates: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    if not candidates:
        return []
    model = _get_model()
    pairs = [(question, f"{c.context_summary}\n{c.text}") for c in candidates]
    scores = model.predict(pairs)
    ranked = sorted(zip(candidates, scores), key=lambda cs: cs[1], reverse=True)
    return [c.model_copy(update={"score": float(s)}) for c, s in ranked[:top_k]]
