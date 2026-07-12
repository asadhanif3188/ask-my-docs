"""Ragas eval runner — the CI gate.

Runs every golden_set.jsonl question through the live retrieval+generation
pipeline, scores with Ragas (faithfulness, answer relevance, context recall),
prints a summary table, and exits non-zero if any gate threshold is missed.

Usage: uv run python -m evals.run_evals
Grow golden_set.jsonl to >= 50 questions before trusting the numbers.
"""

import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings

GOLDEN_SET = Path(__file__).parent / "golden_set.jsonl"


def load_golden_set() -> list[dict]:
    cases = [json.loads(line) for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    unfilled = [c["id"] for c in cases if "REPLACE" in json.dumps(c)]
    if unfilled:
        print(f"golden set has unfilled placeholder cases: {unfilled}")
        sys.exit(2)
    return cases


async def run_case(case: dict) -> dict:
    """Run one golden question through hybrid_retrieve -> rerank -> generate_answer
    and return {question, answer, contexts, ground_truth} for Ragas.
    TODO(phase1): implement against the live pipeline (needs a seeded eval org)."""
    raise NotImplementedError


async def main() -> None:
    settings = get_settings()
    cases = load_golden_set()
    results = [await run_case(c) for c in cases]

    # TODO(phase1): score with ragas.evaluate() — faithfulness, answer_relevancy, context_recall
    scores = {"faithfulness": 0.0, "context_recall": 0.0, "answer_relevancy": 0.0}

    print(json.dumps({"n_cases": len(results), **scores}, indent=2))

    failed = []
    if scores["faithfulness"] < settings.eval_min_faithfulness:
        failed.append(f"faithfulness {scores['faithfulness']:.2f} < {settings.eval_min_faithfulness}")
    if scores["context_recall"] < settings.eval_min_context_recall:
        failed.append(f"context_recall {scores['context_recall']:.2f} < {settings.eval_min_context_recall}")

    if failed:
        print("EVAL GATE FAILED: " + "; ".join(failed))
        sys.exit(1)
    print("eval gate passed")


if __name__ == "__main__":
    asyncio.run(main())
