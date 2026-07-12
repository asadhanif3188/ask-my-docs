from fastapi import APIRouter, Depends

from app.auth import Principal, get_principal
from app.db import get_pool

router = APIRouter()


@router.get("/documents")
async def list_documents(principal: Principal = Depends(get_principal)) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, source_uri, title, status, updated_at
        FROM documents
        WHERE org_id = $1 AND status <> 'deleted'
        ORDER BY updated_at DESC
        """,
        principal.org_id,
    )
    return [dict(r) for r in rows]


@router.delete("/documents/{document_id}")
async def delete_document(document_id: int, principal: Principal = Depends(get_principal)) -> dict:
    """Mark deleted and enqueue chunk/embedding removal (delete propagation is
    verified by tests/test_ingestion_lifecycle.py)."""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            "UPDATE documents SET status='deleted', updated_at=now() "
            "WHERE id=$1 AND org_id=$2 RETURNING id",
            document_id,
            principal.org_id,
        )
        if updated is None:
            return {"deleted": False}
        await conn.execute(
            "INSERT INTO ingestion_jobs (document_id, kind) VALUES ($1, 'delete')", document_id
        )
    return {"deleted": True}
