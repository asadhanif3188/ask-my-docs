import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness+readiness in one: the API is useless without Postgres, so a
    DB that fails to answer is reported as 503 (orchestrators pull the pod out
    of rotation) rather than falling through to a plain-text 500.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        logger.error("healthz: database unreachable: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unavailable"})

    return JSONResponse(status_code=200, content={"status": "ok"})
