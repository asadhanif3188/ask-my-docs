"""Background job worker on Postgres FOR UPDATE SKIP LOCKED — no broker needed.

Run with: uv run python -m app.ingestion.jobs
Scaling ceiling (documented in README "10x" section): fine to ~tens of jobs/sec;
past that, move to a real queue.
"""

import asyncio

from app.db import get_pool

POLL_INTERVAL_S = 2.0
MAX_ATTEMPTS = 3


async def claim_next_job(conn) -> dict | None:
    row = await conn.fetchrow(
        """
        UPDATE ingestion_jobs
        SET status='running', attempts=attempts+1, started_at=now()
        WHERE id = (
            SELECT id FROM ingestion_jobs
            WHERE status='queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, document_id, kind, attempts
        """
    )
    return dict(row) if row else None


async def process_job(conn, job: dict) -> None:
    if job["kind"] == "delete":
        # Chunks go via ON DELETE CASCADE when the tombstoned document row is purged;
        # here we remove chunks immediately and keep the tombstone.
        await conn.execute("DELETE FROM chunks WHERE document_id=$1", job["document_id"])
    elif job["kind"] in ("ingest", "reingest"):
        # TODO(phase1): resolve source_uri to a fetchable path and call ingest_document
        raise NotImplementedError("wire ingest/reingest to pipeline.ingest_document")


async def run_worker() -> None:
    pool = await get_pool()
    while True:
        async with pool.acquire() as conn:
            async with conn.transaction():
                job = await claim_next_job(conn)
                if job is None:
                    pass
                else:
                    try:
                        await process_job(conn, job)
                        await conn.execute(
                            "UPDATE ingestion_jobs SET status='done', finished_at=now() WHERE id=$1",
                            job["id"],
                        )
                    except Exception as exc:
                        status = "failed" if job["attempts"] >= MAX_ATTEMPTS else "queued"
                        await conn.execute(
                            "UPDATE ingestion_jobs SET status=$2, last_error=$3 WHERE id=$1",
                            job["id"], status, str(exc),
                        )
        if job is None:
            await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run_worker())
