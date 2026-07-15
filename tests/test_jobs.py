"""process_job(): ingest/reingest dispatch to pipeline.ingest_document, with
permanent-vs-transient failure handling for a missing source file. Uses a real
Postgres connection (see conftest.py) and mocks the summary + embedding LLM
calls so no real model or API key is needed.
"""

import pytest

from app.ingestion import jobs, pipeline
from tests.conftest import make_pdf_bytes

FAKE_SUMMARY = "A short fixture document about ingestion."


@pytest.fixture
def fixture_pdf(tmp_path):
    path = tmp_path / "fixture.pdf"
    path.write_bytes(make_pdf_bytes("Hello ingestion world"))
    return path


@pytest.fixture(autouse=True)
def _mock_llm_calls(monkeypatch):
    async def fake_summarize(full_text, org_id=None):
        return FAKE_SUMMARY

    def fake_embed(texts):
        return [[0.0] * 1024 for _ in texts]

    # pipeline.py imports these names directly, so patch pipeline's references.
    monkeypatch.setattr(pipeline, "summarize_document", fake_summarize)
    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)


async def _make_org_and_document(pool, source_uri: str) -> tuple[int, int]:
    async with pool.acquire() as conn:
        org_id = await conn.fetchval(
            "INSERT INTO orgs (name) VALUES ($1) "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            "test-jobs-org",
        )
        doc_id = await conn.fetchval(
            "INSERT INTO documents (org_id, source_uri, content_hash, status) "
            "VALUES ($1, $2, 'pending-hash', 'pending') "
            "ON CONFLICT (org_id, source_uri) DO UPDATE SET status='pending' RETURNING id",
            org_id, source_uri,
        )
    return org_id, doc_id


async def _enqueue(pool, document_id: int, kind: str = "ingest") -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO ingestion_jobs (document_id, kind) VALUES ($1, $2) RETURNING id",
            document_id, kind,
        )


async def _run_one_iteration(pool) -> None:
    """Mirrors run_worker()'s per-iteration claim/process/finalize logic exactly
    (see app/ingestion/jobs.run_worker) so this test exercises real worker
    semantics without invoking its infinite polling loop."""
    async with pool.acquire() as conn, conn.transaction():
        job = await jobs.claim_next_job(conn)
        assert job is not None, "expected a queued job to claim"
        try:
            await jobs.process_job(conn, job)
            await conn.execute(
                "UPDATE ingestion_jobs SET status='done', finished_at=now() WHERE id=$1",
                job["id"],
            )
        except Exception as exc:
            permanent = isinstance(exc, jobs.PermanentIngestionError)
            status = "failed" if permanent or job["attempts"] >= jobs.MAX_ATTEMPTS else "queued"
            await conn.execute(
                "UPDATE ingestion_jobs SET status=$2, last_error=$3 WHERE id=$1",
                job["id"], status, str(exc),
            )


async def test_ingest_job_succeeds_and_populates_chunks(pool, fixture_pdf):
    org_id, doc_id = await _make_org_and_document(pool, str(fixture_pdf))
    job_id = await _enqueue(pool, doc_id, "ingest")

    await _run_one_iteration(pool)

    async with pool.acquire() as conn:
        doc_status = await conn.fetchval("SELECT status FROM documents WHERE id=$1", doc_id)
        job_status = await conn.fetchval("SELECT status FROM ingestion_jobs WHERE id=$1", job_id)
        chunk_rows = await conn.fetch(
            "SELECT context_summary, embedding FROM chunks WHERE document_id=$1", doc_id
        )
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)

    assert doc_status == "ready"
    assert job_status == "done"
    assert len(chunk_rows) > 0
    assert all(row["context_summary"] == FAKE_SUMMARY for row in chunk_rows)
    assert all(row["embedding"] is not None for row in chunk_rows)


async def test_ingest_job_with_missing_file_quarantines_and_fails_without_retry(pool):
    org_id, doc_id = await _make_org_and_document(pool, "/nonexistent/path/does-not-exist.pdf")
    job_id = await _enqueue(pool, doc_id, "ingest")

    await _run_one_iteration(pool)

    async with pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status, error FROM documents WHERE id=$1", doc_id)
        job_row = await conn.fetchrow("SELECT status, attempts FROM ingestion_jobs WHERE id=$1", job_id)
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)

    assert doc_row["status"] == "quarantined"
    assert doc_row["error"]
    assert job_row["status"] == "failed"
    assert job_row["attempts"] == 1  # failed on the very first attempt, no retry loop


async def test_ingest_job_with_file_uri_succeeds(pool, fixture_pdf):
    file_uri = fixture_pdf.as_uri()
    org_id, doc_id = await _make_org_and_document(pool, file_uri)
    job_id = await _enqueue(pool, doc_id, "ingest")

    await _run_one_iteration(pool)

    async with pool.acquire() as conn:
        doc_status = await conn.fetchval("SELECT status FROM documents WHERE id=$1", doc_id)
        job_status = await conn.fetchval("SELECT status FROM ingestion_jobs WHERE id=$1", job_id)
        chunk_count = await conn.fetchval("SELECT count(*) FROM chunks WHERE document_id=$1", doc_id)
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)

    assert doc_status == "ready"
    assert job_status == "done"
    assert chunk_count > 0


async def test_ingest_job_with_unsupported_scheme_quarantines_and_fails_without_retry(pool):
    org_id, doc_id = await _make_org_and_document(pool, "https://example.com/doc.pdf")
    job_id = await _enqueue(pool, doc_id, "ingest")

    await _run_one_iteration(pool)

    async with pool.acquire() as conn:
        doc_row = await conn.fetchrow("SELECT status, error FROM documents WHERE id=$1", doc_id)
        job_row = await conn.fetchrow("SELECT status, attempts FROM ingestion_jobs WHERE id=$1", job_id)
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)

    assert doc_row["status"] == "quarantined"
    assert "scheme" in doc_row["error"]
    assert job_row["status"] == "failed"
    assert job_row["attempts"] == 1  # unsupported scheme is permanent, no retry loop
