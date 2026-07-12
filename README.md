# Ask My Docs — Production RAG Service

Multi-tenant RAG over a living document corpus (target domain: SEC 10-K filings) with
verifiable citations, hybrid retrieval, and CI-gated evals.
Built per `../Five(5)AIEngineerProjects_BuildPlan.md`, Project 1.

## Architecture

```
[Client] ──JWT──► [FastAPI]
                     │
                     ▼
              [Query Rewriter]
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
 [Postgres FTS (BM25-like)]  [pgvector dense (BGE-M3)]
        └────────────┬────────────┘
                     ▼
          [RRF fusion → top 50]
                     ▼
        [bge-reranker-base → top 5]
                     ▼
   [LLM generation, citation-enforced JSON]
                     ▼
   [Pydantic validation → block uncited claims]
```

Trust boundary: `org_id` is taken from the JWT only, and every retrieval query
filters by it at the SQL level. Tenant isolation is proven by
`tests/test_tenant_isolation.py`.

Single datastore: PostgreSQL holds vectors (pgvector), full-text indexes,
tenant metadata, and the ingestion job queue (`FOR UPDATE SKIP LOCKED`).

## Quickstart

```bash
cp .env.example .env          # fill in LLM API key
docker compose up -d db
uv sync                       # or: pip install -e ".[dev]"
uv run python -m scripts.migrate
uv run uvicorn app.main:app --reload
```

Ingest documents:

```bash
uv run python -m scripts.ingest --org demo --path ./data/sample_10k.pdf
```

Run evals (CI gate: faithfulness ≥ 0.85, context recall ≥ 0.80):

```bash
uv run python -m evals.run_evals
```

## Project layout

```
app/
  main.py            FastAPI app factory + routers
  config.py          pydantic-settings, all env-driven
  db.py              asyncpg pool
  auth.py            JWT decode → (user_id, org_id) dependency
  models.py          request/response schemas incl. citation contract
  api/               query, documents, health endpoints
  ingestion/         parse → contextual chunk → embed → upsert; job queue
  retrieval/         hybrid search + RRF + reranker
  generation/        LLM call + citation validator
  observability.py   span/cost hooks (wired to Langfuse in Project 3)
migrations/          plain SQL, applied in order
evals/               golden set + Ragas runner
tests/               tenant isolation, ingestion lifecycle, citation validation
scripts/             ingest CLI, migrate
```

## Status

- [ ] Phase 1 — Core: ingestion CLI, query path, 50-question golden set
- [ ] Phase 2 — Production layer: auth/tenancy, ingestion lifecycle, rate limits, fallbacks, CI gate
- [ ] Phase 3 — Evidence: METRICS.md numbers, Graph RAG experiment write-up, INCIDENTS.md

See `METRICS.md` and `INCIDENTS.md` — both must contain real measured output
before this project is called done.
