"""One model per process, however many threads ask for it at once.

@lru_cache memoises a RESULT; it does not serialise the call that produces it. Every
thread that reaches a cold cache at the same moment runs the loader, so N concurrent
first-callers build N copies of a multi-GB model. The eval runner reranks via
asyncio.to_thread, so it fanned out into exactly that: three copies of BGE-M3 and the
cross-encoder loading at once on a 15.7GB laptop, and the process died with a SIGSEGV
before a single eval case ran.

These tests hold the invariant that made that impossible to see from the code: the
underlying constructor runs ONCE, no matter how many threads race it.
"""

import threading
from unittest.mock import MagicMock

import pytest

from app.ingestion import embed as embed_module
from app.retrieval import rerank as rerank_module


@pytest.fixture(autouse=True)
def _clear_model_caches():
    embed_module._load_model.cache_clear()
    rerank_module._load_model.cache_clear()
    yield
    embed_module._load_model.cache_clear()
    rerank_module._load_model.cache_clear()


def test_embedder_loads_once_under_concurrent_first_callers(monkeypatch):
    calls = []

    def fake_transformer(*args, **kwargs):
        calls.append(1)
        threading.Event().wait(0.05)  # simulate a slow multi-GB load
        return MagicMock()

    monkeypatch.setattr(
        "sentence_transformers.SentenceTransformer", fake_transformer, raising=False
    )

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        embed_module._get_model()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"loaded {len(calls)} copies of the embedder, expected 1"


def test_reranker_loads_once_under_concurrent_first_callers(monkeypatch):
    calls = []

    def fake_cross_encoder(*args, **kwargs):
        calls.append(1)
        threading.Event().wait(0.05)
        return MagicMock()

    monkeypatch.setattr(
        "sentence_transformers.CrossEncoder", fake_cross_encoder, raising=False
    )

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        rerank_module._get_model()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"loaded {len(calls)} copies of the reranker, expected 1"
