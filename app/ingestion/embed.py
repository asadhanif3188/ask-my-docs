"""BGE-M3 embeddings, loaded once per process. Used by ingestion (documents)
and retrieval (queries)."""

import asyncio
import threading
from functools import lru_cache

from app.config import get_settings

# See rerank.py: lru_cache alone lets concurrent first-callers each load their own
# copy of a multi-GB model. embed_query() is awaited from many coroutines at once,
# and embed_texts() runs in a thread pool — both can race the cold cache.
_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    return SentenceTransformer(
        settings.embedding_model, cache_folder=settings.hf_cache_dir or None
    )


def _get_model():
    with _LOAD_LOCK:
        return _load_model()


def warm_models() -> None:
    """Load both models once, up front. Callers that fan out (evals) should call this
    before spawning workers so the first N cases don't all race the cold cache."""
    _get_model()
    from app.retrieval.rerank import _get_model as _get_reranker

    _get_reranker()


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True).tolist()


async def embed_query(text: str) -> list[float]:
    # CPU/GPU-bound; keep the event loop free.
    return (await asyncio.to_thread(embed_texts, [text]))[0]
