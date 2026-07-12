"""Apply migrations/*.sql in filename order, tracking applied ones in schema_migrations.

Usage: uv run python -m scripts.migrate
"""

import asyncio
from pathlib import Path

import asyncpg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def main() -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            print(f"applying {path.name}")
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
        print("migrations up to date")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
