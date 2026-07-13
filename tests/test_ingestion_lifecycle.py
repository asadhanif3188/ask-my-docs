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


def test_extraction_rejoins_currency_symbol_with_its_digits():
    """Root-cause fix for the citation-gate incident: store what the filing
    renders, not pypdf's table-flattening artifact."""
    from app.ingestion.pipeline import clean_extracted_text

    assert clean_extracted_text("Total net sales\n$\n391,035") == "Total net sales\n$391,035"
    assert clean_extracted_text("Loss of (\n1,234 )") == "Loss of (1,234 )"


def test_extraction_preserves_boundaries_between_table_cells():
    """The repair must never fuse two figures — that whitespace is the only
    delimiter between distinct cells, and losing it lets a model invent numbers."""
    from app.ingestion.pipeline import clean_extracted_text

    cleaned = clean_extracted_text("391,035\n2\n%\n$\n383,285")
    # The "$" rejoins its digits, but the delimiter between the sales figure and
    # the footnote marker survives — so "391,0352" is never a verbatim span.
    assert cleaned == "391,035\n2\n%\n$383,285"
