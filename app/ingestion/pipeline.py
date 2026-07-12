"""Ingestion pipeline: parse -> contextual chunk -> embed -> upsert.

Lifecycle rules (verified by tests/test_ingestion_lifecycle.py):
- content_hash unchanged  -> skip (no-op re-ingest)
- content_hash changed    -> delete old chunks, insert new ones, same document row
- malformed/unparseable   -> status='quarantined' with error message; never crash the worker
- kind='delete' job       -> remove chunks + embeddings (ON DELETE CASCADE), keep tombstone row
"""

import hashlib
from pathlib import Path

from app.db import get_pool
from app.ingestion.chunking import split_into_chunks, summarize_document
from app.ingestion.embed import embed_texts


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_pdf(path: Path) -> list[tuple[int, str]]:
    """Returns list of (page_number, text). Raises ValueError on unparseable input
    (caller quarantines the document)."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    except Exception as exc:
        raise ValueError(f"Unparseable PDF: {exc}") from exc
    if not any(text.strip() for _, text in pages):
        raise ValueError("PDF contains no extractable text (scanned image? needs OCR)")
    return pages


async def ingest_document(org_id: int, source_uri: str, path: Path, title: str | None = None) -> int:
    """Full pipeline for one document. Returns document_id."""
    data = path.read_bytes()
    digest = content_hash(data)
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(
            "SELECT id, content_hash FROM documents WHERE org_id=$1 AND source_uri=$2",
            org_id, source_uri,
        )
        if existing and existing["content_hash"] == digest:
            return existing["id"]  # unchanged — skip

        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (org_id, source_uri, title, content_hash, status)
            VALUES ($1, $2, $3, $4, 'ingesting')
            ON CONFLICT (org_id, source_uri) DO UPDATE
              SET content_hash = EXCLUDED.content_hash, status = 'ingesting',
                  error = NULL, updated_at = now()
            RETURNING id
            """,
            org_id, source_uri, title, digest,
        )

        try:
            pages = parse_pdf(path)
        except ValueError as exc:
            await conn.execute(
                "UPDATE documents SET status='quarantined', error=$2, updated_at=now() WHERE id=$1",
                doc_id, str(exc),
            )
            return doc_id

        chunks = split_into_chunks(pages)
        summary = await summarize_document("\n".join(t for _, t in pages))
        vectors = embed_texts([f"{summary}\n{c.text}" for c in chunks])

        # Re-ingest: replace chunks atomically within this transaction.
        await conn.execute("DELETE FROM chunks WHERE document_id=$1", doc_id)
        await conn.executemany(
            """
            INSERT INTO chunks (document_id, org_id, chunk_index, page, text, context_summary, embedding)
            VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
            """,
            [
                (doc_id, org_id, c.index, c.page, c.text, summary, str(v))
                for c, v in zip(chunks, vectors)
            ],
        )
        await conn.execute(
            "UPDATE documents SET status='ready', updated_at=now() WHERE id=$1", doc_id
        )

    return doc_id
