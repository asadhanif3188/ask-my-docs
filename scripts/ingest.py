"""Ingestion CLI.

Usage:
    uv run python -m scripts.ingest --org demo --path ./data/sample_10k.pdf
"""

import argparse
import asyncio
from pathlib import Path

from app.db import close_pool, get_pool
from app.ingestion.pipeline import ingest_document


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True, help="org name (created if missing)")
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    pool = await get_pool()
    org_id = await pool.fetchval(
        "INSERT INTO orgs (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name "
        "RETURNING id",
        args.org,
    )

    paths = [args.path] if args.path.is_file() else sorted(args.path.glob("**/*.pdf"))
    for path in paths:
        doc_id = await ingest_document(
            org_id=org_id, source_uri=str(path), path=path, title=args.title or path.stem
        )
        status = await pool.fetchval("SELECT status FROM documents WHERE id=$1", doc_id)
        print(f"{path.name}: document_id={doc_id} status={status}")

    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
