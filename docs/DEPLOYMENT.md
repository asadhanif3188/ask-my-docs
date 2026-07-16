# Deployment & CI/Eval Strategy

## Overview

Ask My Docs uses a two-tier evaluation strategy to balance comprehensive testing with CI speed.

### CI Golden Set (15 cases, ~30s runtime)

**Used:** Every pull request (when `LLM_API_KEY` secret is available)

**Corpus:** 2 self-contained documents in `fixtures/ci_corpus/` (Python guide + database fundamentals)

**File:** `evals/golden_set_ci.jsonl`

**Cases:** 15 answerable + unanswerable cases covering factual/semantic/synthesis categories

**Thresholds:**
- `faithfulness >= 0.85` (from `app/config.py::eval_min_faithfulness`)
- `context_recall >= 0.80` (from `app/config.py::eval_min_context_recall`)
- No hallucinations on unanswerable cases (deterministic check, not Ragas-scored)

**Purpose:** Catch regressions in core retrieval/generation logic on every PR. Fast feedback loop.

### Full Golden Set (50 cases, ~5-10 min runtime)

**Used:** Local development, pre-release verification, manual testing

**Corpus:** 12 real SEC 10-K filings (Apple, Microsoft, NVIDIA; FY2022–2026) in `corpus/`

**File:** `evals/golden_set.jsonl`

**Cases:** 45 answerable (factual/semantic/synthesis) + 5 unanswerable

**Thresholds:** Same as CI

**Purpose:** Comprehensive validation before shipping. Tests against real-world documents with complex financial/legal language.

**Note:** Not in CI by default — the corpus includes SEC EDGAR bulk data (check licensing before redistribution).

## Running Evals

### Local CI reproduction

Reproduce the exact GitHub Actions sequence locally:

```bash
# Full pipeline
uv run python -m scripts.ci_local

# Skip individual stages
uv run python -m scripts.ci_local --skip-lint
uv run python -m scripts.ci_local --skip-test
uv run python -m scripts.ci_local --skip-evals
uv run python -m scripts.ci_local --skip-build

# With verbose output
uv run python -m scripts.ci_local --help
```

Prerequisites:
- Postgres running (via `docker compose up db`)
- `LLM_API_KEY` in environment (Anthropic key for Ragas judge)
- `DATABASE_URL` pointing to local Postgres

### Full eval run

```bash
# Full 50-case run
uv run python -m evals.run_evals

# Options
uv run python -m evals.run_evals --golden-set full  # explicit (default)
uv run python -m evals.run_evals --limit 5          # first 5 cases only
uv run python -m evals.run_evals --case-id g001 g002 g003  # specific cases
uv run python -m evals.run_evals --no-score         # run pipeline, skip Ragas (free)
uv run python -m evals.run_evals --concurrency 8    # parallel cases (default 4)
```

## GitHub Actions Workflow

### Trigger conditions

**Lint & test:** Every push to main and all PRs

**Eval gate:** PRs only (not pushes to main), skips automatically on fork PRs (no secret access)

**Build check:** After evals pass (or if evals are skipped)

### Caching

- **uv dependencies:** Cached per `uv.lock`, restored if unchanged
- **Embedding model:** BGE-M3 cached in `~/.cache/huggingface` (lazy load on first run, then cache hit)

Cache keys:
- `${{ runner.os }}-uv-${{ hashFiles('**/uv.lock') }}`
- `${{ runner.os }}-hf-bge-m3`

### Secret handling

**`LLM_API_KEY`:**
- Used by Ragas judge in `evals/run_evals.py`
- Set in GitHub repo secrets: Settings → Security → Secrets and variables → Repository secrets
- On fork PRs: secret is unavailable → workflow prints `::warning::` and skips evals (does not fail)

## Adding new CI test cases

To extend the CI golden set with more test cases:

1. Add documents to `fixtures/ci_corpus/` (ideally public-domain or self-written; ~5-10KB each)
2. Create test cases in `evals/golden_set_ci.jsonl` referencing only those documents
3. Run locally to verify:
   ```bash
   uv run python -m evals.run_evals --golden-set ci --case-id ci<your_id>
   ```
4. Commit and push — next PR run will include them

Example case structure (from `golden_set_ci.jsonl`):
```json
{
  "id": "ci001",
  "question": "In what year was Python first released?",
  "ground_truth": "Python was first released in 1991.",
  "kind": "factual",
  "source_document": "fixtures\\ci_corpus\\Python_Guide_Intro.pdf",
  "source_pages": [1],
  "chunk_ids": [1],
  "expected_passage": "first released in 1991",
  "expect_refusal": false
}
```

## Troubleshooting

### Evals fail locally but pass in CI

**Possible causes:**
- Environment variable mismatch (`LLM_MODEL`, `embedding_model`, etc.) — check `app/config.py`
- Model version mismatch (embeddings are deterministic; torch/ONNX versions matter)
- Database state — evals write to the `eval_org`, ensure a clean DB or different org name

**Fix:**
```bash
# Use exact same config as CI
export DATABASE_URL=postgresql://rag:rag@localhost:5432/askmydocs
export JWT_SECRET=ci-only-secret
export LLM_API_KEY=<your-anthropic-key>

uv run python -m scripts.ci_local
```

### Eval gate is red but should be green

Check the workflow run details in GitHub Actions:

1. **Seeding failed:** Check Postgres is running, `DATABASE_URL` is correct, migrations applied
2. **LLM API failed:** Check `LLM_API_KEY` is set and valid (Ragas judge needs it)
3. **Retrieval regressed:** Check recent changes to `app/retrieval/`, `app/config.py` (thresholds)
4. **Generation regressed:** Check `app/generation/`, `app/config.py` (LLM model, system prompt)

Replay locally:
```bash
uv run python -m evals.run_evals --golden-set ci --no-score  # free pipeline check
uv run python -m evals.run_evals --golden-set ci --dump /tmp/ci_results.json  # inspect raw
```

### Fork PR: eval gate skipped

This is intentional — fork PRs cannot access the `LLM_API_KEY` secret (security). The workflow will print:

```
::warning::LLM_API_KEY secret not available (fork PR?). Eval gate skipped.
```

**To test evals on your fork:**
1. Add your own `LLM_API_KEY` secret to your fork's repo settings
2. Re-run the workflow

## Performance notes

**CI golden set:**
- 15 cases, ~2-3 per kind (factual/semantic/synthesis/unanswerable)
- Runs in ~30s on GitHub-hosted runners (4 cores, 16GB RAM)
- Embedding model loads once, then is cached

**Full golden set:**
- 50 cases across 12 documents, ~1.8K chunks
- Runs in ~5-10 min depending on runner concurrency and LLM provider latency
- Ragas judge (Claude) dominates wall-clock time, not retrieval

To speed up local evals:
```bash
uv run python -m evals.run_evals --golden-set ci --concurrency 8  # parallelism
uv run python -m evals.run_evals --golden-set ci --no-score       # skip Ragas
```

## Next steps

- **Automate golden set updates:** New questions → append to `golden_set_ci.jsonl`, run via PR
- **Eval metrics dashboard:** Export results to a time-series DB for regression detection
- **Parallel evals in CI:** Run full and CI sets in parallel (need separate DB/org)
- **Scheduled deep evals:** Nightly full runs against main, alert if regression detected
