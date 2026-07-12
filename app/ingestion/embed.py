"""BGE-M3 embeddings, loaded once per process. Used by ingestion (documents)
and retrieval (queries)."""

import asyncio
from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True).tolist()


async def embed_query(text: str) -> list[float]:
    # CPU/GPU-bound; keep the event loop free.
    return (await asyncio.to_thread(embed_texts, [text]))[0]
