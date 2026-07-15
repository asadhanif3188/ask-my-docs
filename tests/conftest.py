"""Shared fixtures. DB-backed tests require the docker compose Postgres to be up:
    docker compose up -d db && uv run python -m scripts.migrate
"""

import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.db import close_pool, get_pool


def make_pdf_bytes(text: str) -> bytes:
    """Builds a minimal single-page PDF with real extractable text, entirely via
    pypdf (already a project dependency) — no reportlab needed. Uses pypdf's
    private `_add_object` (no public "draw text" API exists); if a future pypdf
    release renames/removes it, this helper (not app code) breaks first."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)

    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 18 Tf 10 100 Td ({text}) Tj ET".encode())
    stream_ref = writer._add_object(stream)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_ref = writer._add_object(font)

    resources = DictionaryObject()
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_dict

    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = stream_ref

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
async def pool():
    p = await get_pool()
    yield p
    await close_pool()


@pytest.fixture
async def two_orgs(pool):
    """Two isolated orgs, each with one document and one chunk. Cleaned up after."""
    org_ids = []
    async with pool.acquire() as conn:
        for name in ("test-org-a", "test-org-b"):
            org_id = await conn.fetchval(
                "INSERT INTO orgs (name) VALUES ($1) "
                "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                name,
            )
            doc_id = await conn.fetchval(
                "INSERT INTO documents (org_id, source_uri, content_hash, status) "
                "VALUES ($1, $2, 'testhash', 'ready') "
                "ON CONFLICT (org_id, source_uri) DO UPDATE SET status='ready' RETURNING id",
                org_id, f"test://{name}/doc",
            )
            await conn.execute(
                "INSERT INTO chunks (document_id, org_id, chunk_index, text) "
                "VALUES ($1, $2, 0, $3) ON CONFLICT (document_id, chunk_index) DO NOTHING",
                doc_id, org_id, f"secret revenue figure for {name} is 42 million dollars",
            )
            org_ids.append(org_id)

    yield tuple(org_ids)

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM documents WHERE org_id = ANY($1::bigint[])", org_ids
        )
        await conn.execute("DELETE FROM orgs WHERE id = ANY($1::bigint[])", org_ids)


# Deterministic stand-in for a real BGE-M3 embedding: the point of the dense
# tenant-isolation test is SQL scoping, not embedding quality (see
# tests/test_tenant_isolation.py). QUERY_VECTOR is what a mocked embed_query()
# returns; org B's stored vector is set identical to it (cosine distance 0),
# so if the SQL org_id filter is ever dropped, org B's chunk becomes the
# closest neighbor in the *whole table* and would be the first row to leak
# into org A's results — a loud, unmissable failure. Kept as a list (not a
# tuple) because app.retrieval.hybrid's _dense_search formats it via
# str(vector) into a pgvector literal, which requires bracket syntax.
EMBEDDING_DIM = 1024
QUERY_VECTOR = [0.0] * EMBEDDING_DIM
QUERY_VECTOR[0] = 1.0


def _vector_at_similarity(cosine_sim: float) -> list[float]:
    """A unit vector in the plane spanned by axes 0/1 with the given cosine
    similarity to QUERY_VECTOR (which lies purely on axis 0)."""
    v = [0.0] * EMBEDDING_DIM
    v[0] = cosine_sim
    v[1] = (1 - cosine_sim**2) ** 0.5
    return v


@pytest.fixture
async def two_orgs_with_embeddings(two_orgs, pool):
    """Layers deterministic embeddings onto two_orgs' fixture chunks. Org A's
    vector is a plausible-but-not-exact match to QUERY_VECTOR (cos_sim=0.8);
    org B's vector is engineered to be the nearest possible neighbor
    (cos_sim=1.0, identical to the query) so org-scoping is the *only* thing
    keeping it out of org A's results.
    """
    org_a, org_b = two_orgs
    vectors = {org_a: _vector_at_similarity(0.8), org_b: _vector_at_similarity(1.0)}

    async with pool.acquire() as conn:
        for org_id, vector in vectors.items():
            await conn.execute(
                "UPDATE chunks SET embedding = $1::vector WHERE org_id = $2",
                str(vector),
                org_id,
            )

    return two_orgs
