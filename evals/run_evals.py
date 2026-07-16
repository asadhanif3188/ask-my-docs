"""Ragas eval runner — the CI gate.

Runs every golden_set.jsonl question through the live retrieval+generation
pipeline, scores with Ragas (faithfulness, answer relevance, context recall),
prints a per-case + per-kind table, and exits non-zero if a gate threshold is missed.

Usage:
    uv run python -m evals.run_evals                  # full 50-case run
    uv run python -m evals.run_evals --limit 5        # cheap smoke run
    uv run python -m evals.run_evals --case-id g047   # one case

The path under test is deliberately the *production* one: this calls the same
hybrid_retrieve -> rerank -> generate_answer sequence as app/api/query.py, against
a normal tenant (evals/seed_eval_org.py), with the same degraded-retrieval
fallback. An eval that took a private shortcut would measure a system nobody runs.

Two things the golden set forces this runner to handle, which a naive
question->score loop gets wrong:

1. The 5 `unanswerable` cases have no correct *answer* — the correct behaviour is
   an empty claim list. Ragas cannot score that (the faithfulness of "" is
   meaningless), so they are scored deterministically instead: any non-empty claim
   set on an unanswerable question is a hallucination, full stop. They are reported
   as their own gate, not averaged into faithfulness, where a correct refusal would
   look like a bad answer and *lower* the score.

2. A case that fails to produce an answer at all (citation gate rejected it, API
   down) is not silently dropped from the mean — dropping failures is how an eval
   suite reports 0.95 faithfulness on the 3 questions it managed to answer. Those
   cases score 0 and stay in the denominator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from app.config import get_settings
from app.db import close_pool
from app.generation.generate import GenerationUnavailable, generate_answer
from app.retrieval.hybrid import RetrievalDegraded, RetrievalUnavailable, hybrid_retrieve
from app.retrieval.rerank import rerank
from app.retrieval.rewrite import rewrite_query
from evals.seed_eval_org import seed_eval_org

GOLDEN_SET_FULL = Path(__file__).parent / "golden_set.jsonl"
GOLDEN_SET_CI = Path(__file__).parent / "golden_set_ci.jsonl"

# Statuses a case can end in. Only ANSWERED yields text worth scoring.
ANSWERED = "answered"    # >=1 validated, cited claim
REFUSED = "refused"      # model returned {"claims": []} — the correct unanswerable answer
BLOCKED = "blocked"      # citation validator rejected the answer: the gate WORKING
ERROR = "error"          # LLM/API failure — inconclusive, not a verdict on the system
NO_CONTEXT = "no_context"
RETRIEVAL_DOWN = "retrieval_unavailable"


def load_golden_set(golden_set: Path) -> list[dict]:
    cases = [json.loads(line) for line in golden_set.read_text(encoding="utf-8").splitlines() if line.strip()]
    unfilled = [c["id"] for c in cases if "REPLACE" in json.dumps(c)]
    if unfilled:
        print(f"golden set has unfilled placeholder cases: {unfilled}")
        sys.exit(2)
    return cases


async def run_case(case: dict, org_id: int) -> dict:
    """question -> hybrid_retrieve -> rerank -> generate_answer.

    Returns the row Ragas needs ({question, answer, contexts, ground_truth}) plus the
    status and chunk_ids — which is what makes a failed case debuggable, and what
    lets us ask which chunks actually surfaced.
    """
    settings = get_settings()
    question = case["question"]
    row: dict = {
        "id": case["id"],
        "kind": case["kind"],
        "question": question,
        "ground_truth": case["ground_truth"],
        "expect_refusal": case.get("expect_refusal", False),
        "response": "",
        "contexts": [],
        "chunk_ids": [],
        "degraded": False,
        "detail": "",
    }

    rewritten = await rewrite_query(question)

    try:
        candidates = await hybrid_retrieve(
            org_id=org_id,
            dense_query=rewritten.dense,
            fts_query=rewritten.fts,
            top_k=settings.retrieve_top_k,
        )
    except RetrievalDegraded as exc:
        candidates = exc.fallback_results  # FTS-only, same contract as the API
        row["degraded"] = True
    except RetrievalUnavailable as exc:
        row["status"], row["detail"] = RETRIEVAL_DOWN, str(exc)[:120]
        return row

    if not candidates:
        row["status"] = NO_CONTEXT
        return row

    # rerank is a sync cross-encoder forward pass; run it off the event loop so the
    # semaphore's concurrency is real and not serialised behind CPU work.
    top_chunks = await asyncio.to_thread(rerank, question, candidates, settings.rerank_top_k)

    # Contexts = what the model was actually shown, summary included, mirroring
    # generate._format_sources. Scoring recall against a cleaner context than the
    # model saw would measure a pipeline we don't ship.
    row["contexts"] = [f"{c.context_summary}\n{c.text}".strip() for c in top_chunks]
    row["chunk_ids"] = [c.chunk_id for c in top_chunks]

    try:
        answer = await generate_answer(
            question, top_chunks, degraded=row["degraded"], org_id=org_id
        )
    except GenerationUnavailable as exc:
        # Distinguish "our validator refused to serve this" from "Anthropic 500'd".
        # Both produce no answer, but only the first is the system behaving correctly.
        row["status"] = BLOCKED if "citation validation" in str(exc) else ERROR
        row["detail"] = str(exc)[:160]
        return row

    row["response"] = " ".join(claim.text for claim in answer.claims)
    row["status"] = ANSWERED if answer.claims else REFUSED
    return row


async def collect(cases: list[dict], org_id: int, concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with sem:
            try:
                row = await run_case(case, org_id)
            except Exception as exc:  # never let one bad case abort the suite
                row = {
                    "id": case["id"], "kind": case["kind"], "question": case["question"],
                    "ground_truth": case["ground_truth"],
                    "expect_refusal": case.get("expect_refusal", False),
                    "status": ERROR, "detail": f"{type(exc).__name__}: {exc}"[:160],
                    "response": "", "contexts": [], "chunk_ids": [], "degraded": False,
                }
            print(f"  {row['id']:>4} {row['kind']:<12} {row['status']}"
                  + (f"  ({row['detail']})" if row.get("detail") else ""))
            return row

    return list(await asyncio.gather(*(one(c) for c in cases)))


def _judge():
    """Ragas judge: Claude for the LLM, the local BGE-M3 for embeddings.

    Ragas defaults to OpenAI for both. There is no OpenAI key here, and no reason to
    add a vendor: the Anthropic key already exists, and answer_relevancy's embeddings
    can come from the same model the retriever uses — locally, for free.
    """
    from langchain_anthropic import ChatAnthropic
    from langchain_core.embeddings import Embeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from app.ingestion.embed import embed_texts

    settings = get_settings()

    class LocalBGE(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return embed_texts(texts)

        def embed_query(self, text: str) -> list[float]:
            return embed_texts([text])[0]

    llm = ChatAnthropic(
        model=settings.eval_judge_model or settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=0.0,
        max_tokens=1024,
        timeout=120,
        max_retries=3,
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(LocalBGE())


def score_answerable(rows: list[dict], concurrency: int) -> dict[str, dict[str, float]]:
    """Ragas-score the cases that produced an answer. Returns {case_id: {metric: score}}.

    Cases that produced no answer are not passed to Ragas (there is nothing to score),
    but they are not forgiven either — the caller folds them in as zeros.
    """
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import answer_relevancy, context_recall, faithfulness
    from ragas.run_config import RunConfig

    scorable = [r for r in rows if r["status"] == ANSWERED and r["response"].strip()]
    if not scorable:
        return {}

    dataset = EvaluationDataset(samples=[
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=r["contexts"],
            response=r["response"],
            reference=r["ground_truth"],
        )
        for r in scorable
    ])

    judge_llm, judge_emb = _judge()
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_recall],
        llm=judge_llm,
        embeddings=judge_emb,
        run_config=RunConfig(max_workers=concurrency),
    )

    df = result.to_pandas()
    scores: dict[str, dict[str, float]] = {}
    for row, (_, scored) in zip(scorable, df.iterrows()):
        out: dict[str, float] = {}
        for metric in ("faithfulness", "answer_relevancy", "context_recall"):
            if metric not in df.columns:
                continue
            value = scored[metric]
            # Ragas returns NaN when a judge call fails or a metric is undefined for
            # the sample; NaN != NaN, and letting one through would poison the mean.
            out[metric] = float(value) if value == value else 0.0
        scores[row["id"]] = out
    return scores


def report(rows: list[dict], scores: dict[str, dict[str, float]]) -> tuple[dict, list[str]]:
    answerable = [r for r in rows if not r["expect_refusal"]]
    unanswerable = [r for r in rows if r["expect_refusal"]]

    def metric(row: dict, name: str) -> float:
        # A case that never produced an answer scores 0 and stays in the denominator.
        return scores.get(row["id"], {}).get(name, 0.0)

    print("\n--- per case " + "-" * 62)
    print(f"{'id':<5} {'kind':<13} {'status':<12} {'faith':>6} {'relev':>6} {'recall':>8}")
    for row in rows:
        if row["expect_refusal"]:
            verdict = "HALLUCINATED" if row["status"] == ANSWERED else "ok"
            print(f"{row['id']:<5} {row['kind']:<13} {row['status']:<12} "
                  f"{'-':>6} {'-':>6} {verdict:>8}")
        else:
            print(f"{row['id']:<5} {row['kind']:<13} {row['status']:<12} "
                  f"{metric(row, 'faithfulness'):>6.2f} {metric(row, 'answer_relevancy'):>6.2f} "
                  f"{metric(row, 'context_recall'):>8.2f}")

    by_kind: dict[str, list[dict]] = defaultdict(list)
    for row in answerable:
        by_kind[row["kind"]].append(row)

    print("\n--- per kind " + "-" * 62)
    print(f"{'kind':<13} {'n':>3} {'answered':>9} {'faith':>6} {'relev':>6} {'recall':>8}")
    for kind, group in sorted(by_kind.items()):
        n_answered = sum(1 for r in group if r["status"] == ANSWERED)
        print(f"{kind:<13} {len(group):>3} {n_answered:>9} "
              f"{statistics.mean(metric(r, 'faithfulness') for r in group):>6.2f} "
              f"{statistics.mean(metric(r, 'answer_relevancy') for r in group):>6.2f} "
              f"{statistics.mean(metric(r, 'context_recall') for r in group):>8.2f}")

    # The hallucination gate is a count, not a mean: one fabricated answer to a
    # question the corpus cannot answer is a failure however well the other 49 scored.
    hallucinated = [r["id"] for r in unanswerable if r["status"] == ANSWERED]
    refused = [r for r in unanswerable if r["status"] in (REFUSED, BLOCKED, NO_CONTEXT)]
    inconclusive = [r["id"] for r in unanswerable if r["status"] in (ERROR, RETRIEVAL_DOWN)]
    if unanswerable:
        print(f"\n--- hallucination gate ({len(unanswerable)} unanswerable cases) " + "-" * 29)
        print(f"  refused correctly : {len(refused)}")
        print(f"  HALLUCINATED      : {len(hallucinated)} {hallucinated or ''}")
        if inconclusive:
            print(f"  inconclusive      : {len(inconclusive)} {inconclusive} "
                  f"(API/retrieval error, not a verdict)")

    mean = statistics.mean
    summary = {
        "n_cases": len(rows),
        "n_answerable": len(answerable),
        "n_answered": sum(1 for r in answerable if r["status"] == ANSWERED),
        "faithfulness": mean(metric(r, "faithfulness") for r in answerable) if answerable else 0.0,
        "answer_relevancy": mean(metric(r, "answer_relevancy") for r in answerable) if answerable else 0.0,
        "context_recall": mean(metric(r, "context_recall") for r in answerable) if answerable else 0.0,
        "hallucinated": hallucinated,
    }
    return summary, hallucinated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--case-id", action="append", help="run only these case ids (repeatable)")
    parser.add_argument("--concurrency", type=int, default=4, help="in-flight cases (3-5 is sane)")
    parser.add_argument("--no-score", action="store_true",
                        help="run the pipeline but skip Ragas — free, inspects retrieval only")
    parser.add_argument("--dump", type=Path, default=None, help="write raw per-case rows to JSON")
    parser.add_argument("--golden-set", choices=["full", "ci"], default="full",
                        help="which golden set to run (full=50 cases, ci=15 cases for CI)")
    args = parser.parse_args()

    settings = get_settings()
    golden_set_path = GOLDEN_SET_CI if args.golden_set == "ci" else GOLDEN_SET_FULL
    cases = load_golden_set(golden_set_path)
    if args.case_id:
        wanted = set(args.case_id)
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected")
        sys.exit(2)

    async def pipeline() -> list[dict]:
        org_id = await seed_eval_org()

        # Load the embedder and reranker ONCE, before fanning out. Without this the
        # first N concurrent cases each raced the cold model cache and loaded their
        # own copy of a multi-GB model, which killed the process with a SIGSEGV
        # before any case ran. The locks in embed.py/rerank.py make that safe; this
        # makes it fast, by paying the load serially instead of under contention.
        print("warming models...")
        from app.ingestion.embed import warm_models

        await asyncio.to_thread(warm_models)

        print(f"\nrunning {len(cases)} case(s) against org_id={org_id}, "
              f"concurrency={args.concurrency}")
        try:
            return await collect(cases, org_id, args.concurrency)
        finally:
            await close_pool()

    # Ragas' evaluate() drives its own executor, so it runs *outside* the pipeline's
    # event loop rather than nested inside it.
    rows = asyncio.run(pipeline())

    scores = {} if args.no_score else score_answerable(rows, args.concurrency)
    summary, hallucinated = report(rows, scores)

    if args.dump:
        args.dump.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nraw rows -> {args.dump}")

    print("\n" + json.dumps(
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary.items()}, indent=2))

    if args.no_score:
        print("\n--no-score: gate skipped")
        return

    failed = []
    if summary["faithfulness"] < settings.eval_min_faithfulness:
        failed.append(
            f"faithfulness {summary['faithfulness']:.2f} < {settings.eval_min_faithfulness}")
    if summary["context_recall"] < settings.eval_min_context_recall:
        failed.append(
            f"context_recall {summary['context_recall']:.2f} < {settings.eval_min_context_recall}")
    if hallucinated:
        # Not one of the two configured thresholds, but serving a fabricated answer to
        # a question the corpus cannot answer is the failure this project exists to
        # prevent. Exiting 0 on it would make the gate decorative.
        failed.append(f"hallucinated on unanswerable cases: {hallucinated}")

    if failed:
        print("EVAL GATE FAILED: " + "; ".join(failed))
        sys.exit(1)
    print("eval gate passed")


if __name__ == "__main__":
    main()
