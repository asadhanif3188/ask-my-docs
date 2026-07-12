"""Background job worker on Postgres FOR UPDATE SKIP LOCKED — no broker needed.

Run with: uv run python -m app.ingestion.jobs
Scaling ceiling (documented in README "10x" section): fine to ~tens of jobs/sec;
past that, move to a real queue.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from app.db import get_pool
from app.ingestion import pipeline

POLL_INTERVAL_S = 2.0
MAX_ATTEMPTS = 3


class PermanentIngestionError(Exception):
    """A failure that will never succeed on retry (missing source file,
    unsupported URI scheme). Lets run_worker fail the job immediately instead
    of requeuing it up to MAX_ATTEMPTS times."""


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


def _resolve_source_path(source_uri: str) -> Path:
    """Resolve a document's source_uri to a local filesystem path.

    Supports plain filesystem paths — including Windows drive paths like
    "D:\\docs\\a.pdf", which urlparse would otherwise misread as a one-letter
    URI scheme — and file:// URIs. http(s) fetching is out of scope for phase 1;
    such a source_uri raises PermanentIngestionError rather than hanging the
    worker on an unimplemented fetch path.
    """
    parsed = urlparse(source_uri)
    if not parsed.scheme or len(parsed.scheme) == 1:
        return Path(source_uri)
    if parsed.scheme == "file":
        # url2pathname dispatches on the OS this worker runs on (e.g. strips the
        # leading slash before a drive letter on Windows); a file:// URI is only
        # meaningful relative to the host that produced it.
        return Path(url2pathname(parsed.path))
    raise PermanentIngestionError(
        f"unsupported source_uri scheme {parsed.scheme!r}: only local paths and file:// "
        "URIs are supported (http(s) fetching is out of scope for phase 1)"
    )


async def _quarantine(conn, document_id: int, reason: str) -> None:
    await conn.execute(
        "UPDATE documents SET status='quarantined', error=$2, updated_at=now() WHERE id=$1",
        document_id, reason,
    )


async def process_job(conn, job: dict) -> None:
    if job["kind"] == "delete":
        # Chunks go via ON DELETE CASCADE when the tombstoned document row is purged;
        # here we remove chunks immediately and keep the tombstone.
        await conn.execute("DELETE FROM chunks WHERE document_id=$1", job["document_id"])
    elif job["kind"] in ("ingest", "reingest"):
        doc = await conn.fetchrow(
            "SELECT org_id, source_uri, title FROM documents WHERE id=$1", job["document_id"]
        )
        try:
            path = _resolve_source_path(doc["source_uri"])
            if not path.exists():
                raise PermanentIngestionError(f"source file not found: {path}")
        except PermanentIngestionError as exc:
            await _quarantine(conn, job["document_id"], str(exc))
            raise

        # pipeline.ingest_document owns hashing, content-hash-skip, and atomic
        # chunk replacement; the worker stays a thin dispatcher over it. It also
        # commits its own connection/transaction, separate from this function's
        # `conn` (run_worker's job-claim transaction) — safe only because a
        # dropped outer transaction just requeues the job, and ingest_document's
        # content-hash-skip makes a reprocessed job a no-op rather than a
        # duplicate ingest.
        await pipeline.ingest_document(doc["org_id"], doc["source_uri"], path, title=doc["title"])


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
                        # Permanent errors (e.g. missing source file) skip the
                        # MAX_ATTEMPTS requeue and fail immediately.
                        permanent = isinstance(exc, PermanentIngestionError)
                        status = "failed" if permanent or job["attempts"] >= MAX_ATTEMPTS else "queued"
                        await conn.execute(
                            "UPDATE ingestion_jobs SET status=$2, last_error=$3 WHERE id=$1",
                            job["id"], status, str(exc),
                        )
        if job is None:
            await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run_worker())
