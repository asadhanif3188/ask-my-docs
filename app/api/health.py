from fastapi import APIRouter

from app.db import get_pool

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}
