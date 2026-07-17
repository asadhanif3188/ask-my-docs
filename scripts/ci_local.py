#!/usr/bin/env python
"""Run the CI pipeline locally: lint -> test -> seed evals -> run evals -> build check.

Reproduces the exact sequence run in GitHub Actions, including:
1. Linting with ruff
2. Unit tests with pytest
3. Database seeding (requires running Postgres)
4. Eval org seeding with CI corpus
5. Eval gate on CI golden set
6. Build verification

This requires:
- DATABASE_URL pointing to a live Postgres (with pgvector)
- LLM_API_KEY in environment (for evals scoring)

Usage:
    uv run python -m scripts.ci_local            # run all checks
    uv run python -m scripts.ci_local --help     # see all options
"""

import asyncio
import subprocess
import sys
from pathlib import Path

from app.config import get_settings
from app.db import close_pool
from evals.seed_eval_org import seed_eval_org


def run_cmd(cmd: list[str], description: str) -> bool:
    """Run a command and return success status."""
    print(f"\n{'='*70}")
    print(f"{description}")
    print(f"{'='*70}")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"FAILED: {description}")
        return False
    print(f"OK: {description}")
    return True


async def seed_evals_for_ci() -> bool:
    """Seed the eval org with the CI corpus."""
    print(f"\n{'='*70}")
    print("Seeding eval org with CI corpus")
    print(f"{'='*70}")
    try:
        org_id = await seed_eval_org(corpus_dir=Path("fixtures/ci_corpus"))
        print(f"OK: Eval org seeded (org_id={org_id})")
        await close_pool()
        return True
    except Exception as e:
        print(f"FAILED: Could not seed eval org: {e}")
        return False


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-lint",
        action="store_true",
        help="skip ruff linting",
    )
    parser.add_argument(
        "--skip-test",
        action="store_true",
        help="skip pytest",
    )
    parser.add_argument(
        "--skip-evals",
        action="store_true",
        help="skip eval seeding and gate",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="skip build check",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.llm_api_key:
        print("WARNING: LLM_API_KEY not set. Evals will be skipped.")

    all_pass = True

    if not args.skip_lint:
        all_pass = run_cmd(["uv", "run", "ruff", "check", "."], "Lint (ruff check)") and all_pass

    if not args.skip_test:
        all_pass = (
            run_cmd(["uv", "run", "python", "-m", "scripts.migrate"], "Migrate database")
            and all_pass
        )
        all_pass = run_cmd(["uv", "run", "pytest", "-q"], "Unit tests (pytest)") and all_pass

    if not args.skip_evals and settings.llm_api_key:
        all_pass = asyncio.run(seed_evals_for_ci()) and all_pass
        all_pass = (
            run_cmd(
                ["uv", "run", "python", "-m", "evals.run_evals", "--golden-set", "ci"],
                "Eval gate (CI golden set)",
            )
            and all_pass
        )

    if not args.skip_build:
        all_pass = (
            run_cmd(
                ["uv", "run", "python", "-c", "from app.main import app; print('Build OK')"],
                "Build check",
            )
            and all_pass
        )

    print(f"\n{'='*70}")
    if all_pass:
        print("ALL CHECKS PASSED")
        print(f"{'='*70}")
        sys.exit(0)
    else:
        print("SOME CHECKS FAILED")
        print(f"{'='*70}")
        sys.exit(1)


if __name__ == "__main__":
    main()
