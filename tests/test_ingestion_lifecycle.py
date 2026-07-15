"""Ingestion lifecycle: skip-unchanged, re-ingest-on-change, delete propagation,
quarantine on malformed input. These are the 'living corpus' guarantees.

Uses a real Postgres connection (see conftest.py) and mocks the summary + embed
LLM/model calls (same pattern as tests/test_jobs.py) so no real model or API
key is needed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_pdf_bytes


async def _make_org(pool, name: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO orgs (name) VALUES ($1) "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            name,
        )


async def _cleanup_org(pool, org_id: int) -> None:
    async with pool.acquire() as conn:
        # documents -> chunks and documents -> ingestion_jobs both cascade;
        # orgs has no cascade from documents, so it must go last.
        await conn.execute("DELETE FROM documents WHERE org_id=$1", org_id)
        await conn.execute("DELETE FROM orgs WHERE id=$1", org_id)


async def _run_one_job(pool) -> dict:
    """Mirrors run_worker()'s per-iteration claim/process/finalize logic exactly
    (see app/ingestion/jobs.run_worker and tests/test_jobs.py's own helper of
    the same shape) so this exercises real worker semantics without invoking
    its infinite polling loop."""
    from app.ingestion import jobs

    async with pool.acquire() as conn, conn.transaction():
        job = await jobs.claim_next_job(conn)
        assert job is not None, "expected a queued job to claim"
        await jobs.process_job(conn, job)
        await conn.execute(
            "UPDATE ingestion_jobs SET status='done', finished_at=now() WHERE id=$1",
            job["id"],
        )
    return job


async def test_unchanged_document_is_skipped(pool, tmp_path, monkeypatch):
    from app.ingestion import pipeline

    org_id = await _make_org(pool, "lifecycle-unchanged")
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf_bytes("Version one of the document"))

    monkeypatch.setattr(pipeline, "summarize_document", AsyncMock(return_value="summary"))
    embed_spy = MagicMock(side_effect=lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(pipeline, "embed_texts", embed_spy)

    source_uri = "test://unchanged/doc"
    doc_id_1 = await pipeline.ingest_document(org_id, source_uri, path)
    async with pool.acquire() as conn:
        chunk_ids_1 = [
            r["id"] for r in await conn.fetch(
                "SELECT id FROM chunks WHERE document_id=$1 ORDER BY chunk_index", doc_id_1
            )
        ]

    doc_id_2 = await pipeline.ingest_document(org_id, source_uri, path)  # identical bytes
    async with pool.acquire() as conn:
        chunk_ids_2 = [
            r["id"] for r in await conn.fetch(
                "SELECT id FROM chunks WHERE document_id=$1 ORDER BY chunk_index", doc_id_2
            )
        ]
        doc_count = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE org_id=$1 AND source_uri=$2", org_id, source_uri
        )

    await _cleanup_org(pool, org_id)

    assert doc_id_2 == doc_id_1, "re-ingest of unchanged content must reuse the document row"
    assert chunk_ids_1 == chunk_ids_2, "unchanged content must not re-create chunks"
    assert doc_count == 1
    assert embed_spy.call_count == 1, "unchanged content must not trigger a re-embed"


async def test_changed_document_replaces_chunks(pool, tmp_path, monkeypatch):
    from app.ingestion import pipeline

    org_id = await _make_org(pool, "lifecycle-changed")
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf_bytes("Version one of the document"))

    monkeypatch.setattr(pipeline, "summarize_document", AsyncMock(return_value="summary"))
    monkeypatch.setattr(pipeline, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])

    source_uri = "test://changed/doc"
    doc_id_1 = await pipeline.ingest_document(org_id, source_uri, path)
    async with pool.acquire() as conn:
        old_chunk_ids = {
            r["id"] for r in await conn.fetch("SELECT id FROM chunks WHERE document_id=$1", doc_id_1)
        }
        old_hash = await conn.fetchval("SELECT content_hash FROM documents WHERE id=$1", doc_id_1)
    assert old_chunk_ids, "sanity: first ingest must produce chunks"

    path.write_bytes(make_pdf_bytes("Version two, a totally different document"))

    # Concurrent-visibility probe: a separate connection polls this document's
    # chunk count throughout the re-ingest call. ingest_document holds its
    # DELETE + INSERT inside one transaction (verified by reading pipeline.py:
    # the whole function body runs under a single `async with pool.acquire()
    # as conn, conn.transaction():`), so under Postgres READ COMMITTED a
    # concurrent reader on another connection can only ever see the
    # pre-transaction state (old chunks) or the post-commit state (new
    # chunks) — never an in-between empty state. This poller is a best-effort
    # timing probe on top of that structural guarantee, not the sole proof of
    # it: correctness here follows from the transaction boundary, not from
    # how many times this loop happens to interleave.
    observed_counts = []
    stop = asyncio.Event()

    async def poll():
        conn = await pool.acquire()
        try:
            while not stop.is_set():
                observed_counts.append(
                    await conn.fetchval("SELECT count(*) FROM chunks WHERE document_id=$1", doc_id_1)
                )
                await asyncio.sleep(0)
        finally:
            await pool.release(conn)

    poller = asyncio.create_task(poll())
    try:
        doc_id_2 = await pipeline.ingest_document(org_id, source_uri, path)
    finally:
        stop.set()
        await poller

    async with pool.acquire() as conn:
        new_chunk_ids = {
            r["id"] for r in await conn.fetch("SELECT id FROM chunks WHERE document_id=$1", doc_id_2)
        }
        new_hash = await conn.fetchval("SELECT content_hash FROM documents WHERE id=$1", doc_id_2)
        doc_count = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE org_id=$1 AND source_uri=$2", org_id, source_uri
        )

    await _cleanup_org(pool, org_id)

    assert doc_id_2 == doc_id_1, "document row must be reused, not duplicated"
    assert doc_count == 1
    assert new_hash != old_hash
    assert new_chunk_ids, "sanity: re-ingest must produce new chunks"
    assert old_chunk_ids.isdisjoint(new_chunk_ids), "old chunk ids must be gone, not reused"
    assert observed_counts, "poller never got scheduled — probe isn't exercising anything"
    assert 0 not in observed_counts, (
        "a concurrent reader observed zero chunks mid re-ingest — delete+insert "
        "are not atomic (see pipeline.ingest_document)"
    )


async def test_delete_propagates_to_chunks_and_hybrid_retrieve(pool, monkeypatch):
    from app.api.documents import delete_document
    from app.auth import Principal
    from app.retrieval import hybrid
    from app.retrieval.hybrid import hybrid_retrieve

    org_id = await _make_org(pool, "lifecycle-delete")
    async with pool.acquire() as conn:
        doomed_id = await conn.fetchval(
            "INSERT INTO documents (org_id, source_uri, content_hash, status) "
            "VALUES ($1, 'test://delete/doomed', 'h1', 'ready') RETURNING id",
            org_id,
        )
        survivor_id = await conn.fetchval(
            "INSERT INTO documents (org_id, source_uri, content_hash, status) "
            "VALUES ($1, 'test://delete/survivor', 'h2', 'ready') RETURNING id",
            org_id,
        )
        await conn.execute(
            "INSERT INTO chunks (document_id, org_id, chunk_index, text) VALUES ($1, $2, 0, $3)",
            doomed_id, org_id, "confidential merger terms for project condor",
        )
        await conn.execute(
            "INSERT INTO chunks (document_id, org_id, chunk_index, text) VALUES ($1, $2, 0, $3)",
            survivor_id, org_id, "unrelated document about office supplies",
        )

    # The dense branch needs a real embedding model load; dense-branch tenant
    # isolation is deliberately out of scope here too (see
    # test_tenant_isolation.py's own "TODO(phase2): embed fixture chunks"
    # skip) — this test only needs hybrid_retrieve's merge/exclude behavior,
    # which the FTS branch alone already exercises.
    async def empty_dense(*args, **kwargs):
        return []

    monkeypatch.setattr(hybrid, "_dense_search", empty_dense)

    await delete_document(doomed_id, Principal(user_id=1, org_id=org_id))
    job = await _run_one_job(pool)
    assert job["document_id"] == doomed_id

    async with pool.acquire() as conn:
        remaining_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id=$1", doomed_id
        )
        doc_status = await conn.fetchval("SELECT status FROM documents WHERE id=$1", doomed_id)

    doomed_results = await hybrid_retrieve(org_id, dense_query="condor", fts_query="condor", top_k=10)
    survivor_results = await hybrid_retrieve(org_id, dense_query="office", fts_query="office", top_k=10)

    await _cleanup_org(pool, org_id)

    assert remaining_chunks == 0, "deleted document must leave no retrievable chunks"
    assert doc_status == "deleted"
    assert all(c.document_id != doomed_id for c in doomed_results)
    assert any(c.document_id == survivor_id for c in survivor_results), (
        "deletion must be scoped to the deleted document, not the whole org"
    )


async def test_malformed_pdf_is_quarantined_not_crashed(tmp_path):
    from app.ingestion.pipeline import parse_pdf

    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(ValueError):
        parse_pdf(bad)


def test_ingest_fingerprint_changes_when_the_pipeline_version_changes():
    """Skip-if-unchanged compares the file hash, so a pipeline fix would otherwise
    leave every ingested document on stale chunks (INCIDENTS.md) — the fingerprint
    must therefore cover the pipeline, not just the bytes."""
    from app.ingestion import pipeline

    data = b"same file bytes"
    before = pipeline.content_hash(data)

    original = pipeline.INGEST_VERSION
    try:
        pipeline.INGEST_VERSION = original + 1
        after = pipeline.content_hash(data)
    finally:
        pipeline.INGEST_VERSION = original

    assert before != after, "a pipeline bump must invalidate cached ingests"
    assert pipeline.content_hash(data) == before  # and be stable within a version


def test_extraction_rejoins_currency_symbol_with_its_digits():
    """Root-cause fix for the citation-gate incident: store what the filing
    renders, not pypdf's table-flattening artifact."""
    from app.ingestion.pipeline import clean_extracted_text

    assert clean_extracted_text("Total net sales\n$\n391,035") == "Total net sales\n$391,035"
    assert clean_extracted_text("Loss of (\n1,234 )") == "Loss of (1,234)"


def test_extraction_preserves_boundaries_between_table_cells():
    """The repair must never fuse two figures — that whitespace is the only
    delimiter between distinct cells, and losing it lets a model invent numbers."""
    from app.ingestion.pipeline import clean_extracted_text

    cleaned = clean_extracted_text("391,035\n2\n%\n$\n383,285")
    # The "$" rejoins its digits, but the delimiter between the sales figure and
    # the footnote marker survives — so "391,0352" is never a verbatim span.
    assert cleaned == "391,035\n2\n%\n$383,285"
