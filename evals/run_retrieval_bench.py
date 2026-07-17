"""Retrieval-quality benchmark — the headline ablation for METRICS.md.

For every answerable golden case, measures recall@5, recall@10, and MRR of the
known source chunk(s) (golden_set.jsonl `chunk_ids`) under four retrieval
configurations, plus per-config retrieval-stage latency p50/p95. Breaks recall
down by question kind (factual / semantic / synthesis).

This does NOT touch the LLM: query embedding and reranking are the local BGE
models, retrieval is the same Postgres SQL the API runs. It is therefore free to
run and fully reproducible. It reuses the production retrieval code paths —
`_fts_search`, `_dense_search`, `hybrid_retrieve`, `rerank` — rather than
forking the SQL, so a change to how the app retrieves is a change to what this
measures.

Configs:
    dense       BGE-M3 dense search only            (app.retrieval.hybrid._dense_search)
    fts         Postgres full-text only             (app.retrieval.hybrid._fts_search)
    hybrid      FTS + dense fused with RRF          (app.retrieval.hybrid.hybrid_retrieve)
    hybrid_rr   hybrid candidates, cross-encoder    (+ app.retrieval.rerank.rerank)

Recall@k = "did an expected chunk survive into the top k this config would show".
Reported two ways per METRICS.md convention: `any` (>=1 expected chunk in top k)
and `all` (every expected chunk in top k). MRR uses the rank of the first
expected chunk over the full ranked list (depth = retrieve_top_k).

Usage:
    uv run python -m evals.run_retrieval_bench                 # full 45-case run
    uv run python -m evals.run_retrieval_bench --limit 5       # smoke
    uv run python -m evals.run_retrieval_bench --dump out.json # raw per-case rows

Latency note: configs are timed one query at a time (no concurrency) so the
numbers are the cost of a single retrieval, not a throughput figure muddied by
CPU contention between the embedder and the reranker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from app.config import get_settings
from app.db import close_pool
from app.ingestion.embed import warm_models
from app.retrieval.hybrid import _dense_search, _fts_search, hybrid_retrieve
from app.retrieval.rerank import rerank
from evals.seed_eval_org import seed_eval_org

GOLDEN_SET_FULL = Path(__file__).parent / "golden_set.jsonl"

CONFIGS = ("dense", "fts", "hybrid", "hybrid_rr")


def load_answerable(golden_set: Path) -> list[dict]:
    """The recall/MRR metrics are defined only for cases with a known source
    chunk. The 5 unanswerable cases have no `chunk_ids` (the correct retrieval
    result for them is "nothing relevant exists"), so they are not scored here —
    they are the hallucination gate in run_evals.py, a different measurement."""
    cases = [json.loads(line) for line in golden_set.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if not c.get("expect_refusal") and c.get("chunk_ids")]


def _rank_metrics(ranked_ids: list[int], expected: set[int]) -> dict:
    """recall@5/@10 (any + all) and reciprocal rank of the first expected chunk."""
    top5, top10 = ranked_ids[:5], ranked_ids[:10]
    rr = 0.0
    for i, cid in enumerate(ranked_ids):
        if cid in expected:
            rr = 1.0 / (i + 1)
            break
    return {
        "recall5_any": 1.0 if expected & set(top5) else 0.0,
        "recall5_all": 1.0 if expected <= set(top5) else 0.0,
        "recall10_any": 1.0 if expected & set(top10) else 0.0,
        "recall10_all": 1.0 if expected <= set(top10) else 0.0,
        "mrr": rr,
    }


async def run_case(case: dict, org_id: int, top_k: int) -> dict:
    """Run all four configs for one case, capturing ranked chunk_ids and the
    retrieval-stage latency of each. Timed one config at a time."""
    question = case["question"]
    expected = set(case["chunk_ids"])
    ranked: dict[str, list[int]] = {}
    latency_ms: dict[str, float] = {}

    t0 = time.perf_counter()
    dense = await _dense_search(org_id, question, top_k)
    latency_ms["dense"] = (time.perf_counter() - t0) * 1000
    ranked["dense"] = [c.chunk_id for c in dense]

    t0 = time.perf_counter()
    fts = await _fts_search(org_id, question, top_k)
    latency_ms["fts"] = (time.perf_counter() - t0) * 1000
    ranked["fts"] = [c.chunk_id for c in fts]

    # hybrid_retrieve re-runs both branches + RRF: this is exactly the API path,
    # so its latency is the honest cost of hybrid (not dense-time + fts-time).
    t0 = time.perf_counter()
    hybrid = await hybrid_retrieve(org_id, question, question, top_k)
    latency_ms["hybrid"] = (time.perf_counter() - t0) * 1000
    ranked["hybrid"] = [c.chunk_id for c in hybrid]

    # rerank the hybrid candidates (the production +reranker path). top_k=len so
    # the full reranked order is available for MRR; recall@5 uses the first 5.
    t0 = time.perf_counter()
    reranked = await asyncio.to_thread(rerank, question, hybrid, len(hybrid))
    latency_ms["hybrid_rr"] = latency_ms["hybrid"] + (time.perf_counter() - t0) * 1000
    ranked["hybrid_rr"] = [c.chunk_id for c in reranked]

    return {
        "id": case["id"],
        "kind": case["kind"],
        "expected": sorted(expected),
        "metrics": {cfg: _rank_metrics(ranked[cfg], expected) for cfg in CONFIGS},
        "latency_ms": latency_ms,
    }


def _mean(rows: list[dict], cfg: str, metric: str) -> float:
    return statistics.mean(r["metrics"][cfg][metric] for r in rows) if rows else 0.0


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile (small n; avoids interpolation surprises)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def report(rows: list[dict]) -> dict:
    print(f"\n=== retrieval ablation (n={len(rows)} answerable cases) " + "=" * 20)
    print(f"{'config':<12}{'recall@5 any':>13}{'recall@5 all':>13}"
          f"{'recall@10 any':>14}{'MRR':>8}{'p50 ms':>9}{'p95 ms':>9}")
    summary: dict[str, dict] = {}
    for cfg in CONFIGS:
        lat = [r["latency_ms"][cfg] for r in rows]
        s = {
            "recall5_any": _mean(rows, cfg, "recall5_any"),
            "recall5_all": _mean(rows, cfg, "recall5_all"),
            "recall10_any": _mean(rows, cfg, "recall10_any"),
            "mrr": _mean(rows, cfg, "mrr"),
            "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95),
        }
        summary[cfg] = s
        print(f"{cfg:<12}{s['recall5_any']:>12.1%}{s['recall5_all']:>13.1%}"
              f"{s['recall10_any']:>13.1%}{s['mrr']:>8.3f}"
              f"{s['p50_ms']:>9.0f}{s['p95_ms']:>9.0f}")

    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)

    print("\n=== recall@5 (any expected chunk) by kind " + "=" * 27)
    print(f"{'kind':<12}{'n':>4}" + "".join(f"{cfg:>12}" for cfg in CONFIGS))
    per_kind: dict[str, dict] = {}
    for kind in sorted(by_kind):
        grp = by_kind[kind]
        per_kind[kind] = {"n": len(grp)}
        line = f"{kind:<12}{len(grp):>4}"
        for cfg in CONFIGS:
            v = _mean(grp, cfg, "recall5_any")
            per_kind[kind][cfg] = v
            line += f"{v:>11.1%} "
        print(line)

    return {"n": len(rows), "overall": summary, "by_kind": per_kind}


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    cases = load_answerable(GOLDEN_SET_FULL)
    if args.limit:
        cases = cases[: args.limit]
    print(f"scoring {len(cases)} answerable case(s), retrieve_top_k={settings.retrieve_top_k}")

    org_id = await seed_eval_org()
    print("warming models (BGE-M3 embedder + cross-encoder reranker)...")
    await asyncio.to_thread(warm_models)
    # One throwaway pass so first-call graph warmup isn't charged to case 1's latency.
    await run_case(cases[0], org_id, settings.retrieve_top_k)

    rows = []
    try:
        for case in cases:
            row = await run_case(case, org_id, settings.retrieve_top_k)
            rows.append(row)
            r5 = row["metrics"]["hybrid_rr"]["recall5_any"]
            print(f"  {row['id']:>4} {row['kind']:<10} hybrid_rr recall@5={'hit' if r5 else 'MISS'}")
    finally:
        await close_pool()

    summary = report(rows)

    if args.dump:
        args.dump.write_text(
            json.dumps({"summary": summary, "cases": rows}, indent=2), encoding="utf-8"
        )
        print(f"\nraw rows -> {args.dump}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="run only the first N answerable cases")
    parser.add_argument("--dump", type=Path, default=None, help="write per-case rows + summary to JSON")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
