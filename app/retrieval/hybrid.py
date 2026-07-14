"""Hybrid retrieval: Postgres full-text + pgvector dense search, fused with RRF.

Both branches filter by org_id in SQL — this is the tenant isolation boundary.
If the dense branch fails (e.g., embedding model down), raise RetrievalDegraded
carrying FTS-only results so the API can serve a flagged, degraded response.
"""

from app.db import get_pool
from app.ingestion.embed import embed_query
from app.models import RetrievedChunk

RRF_K = 60  # standard smoothing constant


class RetrievalDegraded(Exception):
    """Partial failure: the dense branch is down but FTS still answers, so the
    API can serve a flagged, degraded response."""

    def __init__(self, fallback_results: list[RetrievedChunk]):
        self.fallback_results = fallback_results


class RetrievalUnavailable(Exception):
    """Total failure: FTS — the fallback path itself — is down, which in practice
    means Postgres is unreachable. There is nothing to degrade to, so the API
    must fail honestly (503) rather than pretend no documents matched."""


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion over lists of chunk_ids (best-first)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


async def _fts_search(org_id: int, query: str, limit: int) -> list[RetrievedChunk]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, document_id, page, text, context_summary,
               ts_rank_cd(tsv, websearch_to_tsquery('english', $2)) AS score
        FROM chunks
        WHERE org_id = $1 AND tsv @@ websearch_to_tsquery('english', $2)
        ORDER BY score DESC
        LIMIT $3
        """,
        org_id,
        query,
        limit,
    )
    return [
        RetrievedChunk(
            chunk_id=r["id"], document_id=r["document_id"], page=r["page"],
            text=r["text"], context_summary=r["context_summary"], score=r["score"],
        )
        for r in rows
    ]


async def _dense_search(org_id: int, query: str, limit: int) -> list[RetrievedChunk]:
    vector = await embed_query(query)
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, document_id, page, text, context_summary,
               1 - (embedding <=> $2::vector) AS score
        FROM chunks
        WHERE org_id = $1 AND embedding IS NOT NULL
        ORDER BY embedding <=> $2::vector
        LIMIT $3
        """,
        org_id,
        str(vector),
        limit,
    )
    return [
        RetrievedChunk(
            chunk_id=r["id"], document_id=r["document_id"], page=r["page"],
            text=r["text"], context_summary=r["context_summary"], score=r["score"],
        )
        for r in rows
    ]


async def hybrid_retrieve(
    org_id: int, dense_query: str, fts_query: str, top_k: int
) -> list[RetrievedChunk]:
    try:
        fts_results = await _fts_search(org_id, fts_query, top_k)
    except Exception as exc:
        # FTS is what RetrievalDegraded falls back *to*, so if it fails there is
        # no degraded answer to serve — Postgres itself is almost certainly down.
        raise RetrievalUnavailable(str(exc)) from exc

    try:
        dense_results = await _dense_search(org_id, dense_query, top_k)
    except Exception:
        raise RetrievalDegraded(fallback_results=fts_results[:top_k])

    by_id = {c.chunk_id: c for c in fts_results + dense_results}
    fused = rrf_fuse([
        [c.chunk_id for c in fts_results],
        [c.chunk_id for c in dense_results],
    ])
    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [by_id[cid].model_copy(update={"score": score}) for cid, score in ranked]
