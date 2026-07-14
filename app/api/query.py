import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import Principal, get_principal
from app.config import get_settings
from app.generation.generate import GenerationUnavailable, generate_answer
from app.models import QueryRequest, QueryResponse
from app.retrieval.hybrid import RetrievalDegraded, RetrievalUnavailable, hybrid_retrieve
from app.retrieval.rerank import rerank
from app.retrieval.rewrite import rewrite_query

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, principal: Principal = Depends(get_principal)) -> QueryResponse:
    settings = get_settings()

    # TODO(phase2): rate limit per user + org daily token budget check (token_usage table)

    rewritten = await rewrite_query(req.question)

    degraded = False
    try:
        candidates = await hybrid_retrieve(
            org_id=principal.org_id,
            dense_query=rewritten.dense,
            fts_query=rewritten.fts,
            top_k=settings.retrieve_top_k,
        )
    except RetrievalDegraded as exc:
        candidates = exc.fallback_results  # FTS-only fallback
        degraded = True
    except RetrievalUnavailable as exc:
        # Search backend is down; there is no degraded answer to serve. Fail
        # honestly with a retryable 503 rather than a 200 that would look like
        # "no documents matched". Detail is deliberately generic — exc carries
        # the DSN and must not reach the client.
        logger.error("retrieval unavailable: %s", exc)
        raise HTTPException(
            status_code=503, detail="Search backend unavailable, please retry"
        ) from exc

    if not candidates:
        return QueryResponse(answer=None, sources=[], detail="No relevant documents found")

    top_chunks = rerank(req.question, candidates, top_k=settings.rerank_top_k)

    try:
        answer = await generate_answer(req.question, top_chunks, degraded=degraded)
    except GenerationUnavailable as exc:
        # Log the cause, always. This branch swallows everything from a dead API
        # to a rejected citation, and twice already (INCIDENTS.md: fenced JSON,
        # citation false-rejection) the silence here turned a one-line bug into a
        # manual reproduction hunt — the user-facing detail says "unavailable"
        # even when the real cause was our own validator.
        logger.warning("generation unavailable for org=%s: %s", principal.org_id, exc)
        # Fallback contract: still return retrieved passages, never a 500.
        return QueryResponse(
            answer=None,
            sources=top_chunks,
            detail="Sources found but answer generation is temporarily unavailable",
        )

    return QueryResponse(answer=answer, sources=top_chunks)


@router.post("/feedback/{trace_id}")
async def feedback(trace_id: str, thumbs_up: bool, principal: Principal = Depends(get_principal)):
    # TODO(project3): attach feedback to Langfuse trace; export thumbs-down into golden-set queue
    raise HTTPException(status_code=501, detail="Wired up in Project 3 (observability)")
