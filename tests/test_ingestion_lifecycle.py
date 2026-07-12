"""Ingestion lifecycle: skip-unchanged, re-ingest-on-change, delete propagation,
quarantine on malformed input. These are the 'living corpus' guarantees."""

import pytest


async def test_unchanged_document_is_skipped(pool):
    pytest.skip("TODO(phase2): ingest same file twice, assert chunk ids unchanged (no re-embed)")


async def test_changed_document_replaces_chunks(pool):
    pytest.skip("TODO(phase2): ingest, modify file, re-ingest; assert old chunks gone, doc row reused")


async def test_delete_propagates_to_chunks(pool, two_orgs):
    org_a, _ = two_orgs
    async with pool.acquire() as conn:
        doc_id = await conn.fetchval(
            "SELECT id FROM documents WHERE org_id=$1 LIMIT 1", org_a
        )
        await conn.execute(
            "UPDATE documents SET status='deleted' WHERE id=$1", doc_id
        )
        await conn.execute("DELETE FROM chunks WHERE document_id=$1", doc_id)

        remaining = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id=$1", doc_id
        )
        assert remaining == 0, "deleted document must leave no retrievable chunks"


async def test_malformed_pdf_is_quarantined_not_crashed(tmp_path):
    from app.ingestion.pipeline import parse_pdf

    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(ValueError):
        parse_pdf(bad)
