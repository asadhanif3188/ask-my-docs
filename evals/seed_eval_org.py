"""Idempotent seeding of the org the evals query against.

The eval org is a *normal tenant*. Evals call hybrid_retrieve with its org_id and
hit exactly the org-scoped SQL a production /query hits — there is no eval-only
read path and no auth bypass, so a tenant-isolation regression fails the evals
instead of hiding behind them.

Idempotence is per (org_id, source_uri): a document already `ready` for this org
is skipped, so re-running the seed costs nothing. Note what that does NOT buy —
ingestion is keyed by org, so pointing EVAL_ORG_NAME at a *new* org re-embeds the
whole corpus for it (~2k chunks). That is why the default is configurable: on a
machine where the corpus is already ingested, point the evals at that org and the
seed does no work at all. A fresh machine (or CI) ingests once and is cheap after.
"""

import asyncio
from pathlib import Path

from app.config import get_settings
from app.db import close_pool, get_pool
from app.ingestion.pipeline import ingest_document

CORPUS_DIR = Path("corpus")


async def seed_eval_org(name: str | None = None, corpus_dir: Path = CORPUS_DIR) -> int:
    """Ensure the eval org exists and holds the corpus. Returns its org_id."""
    settings = get_settings()
    org_name = name or settings.eval_org_name
    pool = await get_pool()

    org_id = await pool.fetchval(
        "INSERT INTO orgs (name) VALUES ($1) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        org_name,
    )

    # quarantined counts as settled: the corrupt sample is *meant* to stay
    # quarantined, and retrying it every run would be a permanent no-op cost.
    settled = {
        r["source_uri"]
        for r in await pool.fetch(
            "SELECT source_uri FROM documents "
            "WHERE org_id = $1 AND status IN ('ready', 'quarantined')",
            org_id,
        )
    }

    pending = [p for p in sorted(corpus_dir.glob("**/*.pdf")) if str(p) not in settled]
    if not pending:
        n_chunks = await pool.fetchval("SELECT count(*) FROM chunks WHERE org_id = $1", org_id)
        print(f"eval org {org_name!r} (id={org_id}): {len(settled)} docs settled, "
              f"{n_chunks} chunks — nothing to ingest")
        return org_id

    print(f"eval org {org_name!r} (id={org_id}): ingesting {len(pending)} document(s)")
    for path in pending:
        doc_id = await ingest_document(
            org_id=org_id, source_uri=str(path), path=path, title=path.stem
        )
        status = await pool.fetchval("SELECT status FROM documents WHERE id = $1", doc_id)
        print(f"  {path.name}: document_id={doc_id} status={status}")

    return org_id


async def _main() -> None:
    await seed_eval_org()
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
