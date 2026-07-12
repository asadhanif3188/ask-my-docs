"""Cross-encoder reranking with bge-reranker-base (local, no per-call API cost).

Model is loaded lazily once per process. Measure the latency this stage adds —
it goes in METRICS.md (hybrid vs hybrid+rerank recall AND p95).
"""

from functools import lru_cache

from app.config import get_settings
from app.models import RetrievedChunk


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().reranker_model)


def rerank(question: str, candidates: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    if not candidates:
        return []
    model = _get_model()
    pairs = [(question, f"{c.context_summary}\n{c.text}") for c in candidates]
    scores = model.predict(pairs)
    ranked = sorted(zip(candidates, scores), key=lambda cs: cs[1], reverse=True)
    return [c.model_copy(update={"score": float(s)}) for c, s in ranked[:top_k]]
