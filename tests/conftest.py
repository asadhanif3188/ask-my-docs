"""Shared fixtures. DB-backed tests require the docker compose Postgres to be up:
    docker compose up -d db && uv run python -m scripts.migrate
"""

import pytest

from app.db import close_pool, get_pool


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
