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
UV_HTTP_TIMEOUT=180 uv sync --extra dev   # or: pip install -e ".[dev]"
                                           # extend timeout: torch/numpy/scipy are large downloads
uv run python -m scripts.migrate
uv run uvicorn app.main:app --reload
```

> **Windows note:** if `uv run pytest` fails with `Access is denied`, it's AV/Windows
> blocking the shim exe — use `uv run python -m pytest` instead.

> **Model weights:** the embedder (BGE-M3) and reranker together are ~5.4GB, downloaded
> on first use. Set `HF_CACHE_DIR` in `.env` to keep them off the system drive
> (e.g. `HF_CACHE_DIR=D:/hf-cache/huggingface/hub`); leave it blank for the HF default.

Ingest documents:

```bash
uv run python -m scripts.ingest --org demo --path ./data/sample_10k.pdf
```

Run evals (CI gate: faithfulness ≥ 0.85, context recall ≥ 0.80):

```bash
uv run python -m evals.run_evals
```

Fetch a real demo corpus (SEC 10-K filings for AAPL, MSFT, NVDA) and mint a dev token:

```bash
uv run --extra corpus python -m scripts.fetch_corpus     # -> ./corpus/*.pdf (gitignored)
uv run python -m scripts.ingest --org demo --path ./corpus
uv run python -m scripts.dev_token --org-id <id-printed-by-ingest>
```

## Demo

End-to-end proof run against 12 real SEC 10-K filings (AAPL/MSFT/NVDA, FY2022–2026)
plus one deliberately truncated PDF:

- **12/12 real documents ingested to `ready`**, 1,873 chunks total, all embedded (BGE-M3).
- **1 document quarantined** with a recorded reason: `Unparseable PDF: Stream has ended unexpectedly`.
- **Real `/query` round-trip** — `POST /v1/query {"question": "What was Apple's total net revenue for fiscal year 2024?"}`:

```json
{
  "answer": {
    "claims": [
      {
        "text": "Apple's total net sales for fiscal year 2024 were $391,035 million.",
        "citations": [
          {
            "chunk_id": 514,
            "document_id": 61,
            "page": 54,
            "quote": "Total net sales\n$\n391,035"
          }
        ]
      }
    ],
    "degraded": false
  },
  "sources": [
    {
      "chunk_id": 551,
      "document_id": 61,
      "page": 85,
      "text": "$\n383,285\n$\n394,328\n(1)\nServices net sales include amortization of the deferred value of services bundled in the sales price of\ncertain products. Total net sales include $\n7.7\nbillion of revenue recognized in 2024 that was included in deferred revenue as of September 30, 2023,\n$\n8.2\nbillion of revenue recognized in 2023 that was included in deferred revenue as of September 24, 2022,\nand $\n7.5\nbillion of revenue recognized in 2022 that was included in deferred revenue as of September 25, 2021. Apple Inc. | 2024 Form 10-K | 35\nThe Company’s proportion of net sales by disaggregated revenue source was generally consistent for\neach reportable segment in Note 13, “Segment Information and Geographic Data” for 2024, 2023 and\n2022, except in Greater China, where iPhone revenue represented a moderately higher proportion of\nnet sales. As of September 28, 2024 and September 30, 2023, the Company had total deferred revenue of $\n12.8\nbillion and $\n12.1\nbillion, respectively. As of September 28, 2024, the Company expects\n64\n% of total deferred revenue to be realized in less than a year,\n25\n% within one-to-two years,\n9\n% within two-to-three years and\n2\n% in greater than three years. Note 3 –\nEarnings Per Share\nThe following table shows the computation of basic and diluted earnings per share for 2024, 2023 and\n2022 (net income in millions and shares in thousands):\n2024\n2023\n2022",
      "context_summary": "Apple Inc. 10-K annual report for fiscal year 2024 ending September 28, 2024, covering financial statements, revenue by product segment (iPhone, Mac, iPad, Wearables), marketable securities, debt, in…",
      "score": 0.996483325958252
    },
    {
      "chunk_id": 514,
      "document_id": 61,
      "page": 54,
      "text": "%\n25,977\nRest of Asia Pacific\n30,658\n4\n%\n29,615\n1\n%\n29,375\nTotal net sales\n$\n391,035\n2\n%\n$\n383,285\n(3)\n%\n$\n394,328\nAmericas\nAmericas net sales increased during 2024 compared to 2023 due primarily to higher net sales of\nServices. Europe\nEurope net sales increased during 2024 compared to 2023 due primarily to higher net sales of Services\nand iPhone. Greater China\nGreater China net sales decreased during 2024 compared to 2023 due primarily to lower net sales of\niPhone and iPad. The weakness in the renminbi relative to the U.S. dollar had an unfavorable\nyear-over-year impact on Greater China net sales during 2024. Japan\nJapan net sales increased during 2024 compared to 2023 due primarily to higher net sales of iPhone. The weakness in the yen relative to the U.S. dollar had an unfavorable year-over-year impact on Japan\nnet sales during 2024. Rest of Asia Pacific\nRest of Asia Pacific net sales increased during 2024 compared to 2023 due primarily to higher net\nsales of Services. The weakness in foreign currencies relative to the U.S. dollar had a net unfavorable\nyear-over-year impact on Rest of Asia Pacific net sales during 2024. Apple Inc. | 2024 Form 10-K | 22\nProducts and Services Performance\nThe following table shows net sales by category for 2024, 2023 and 2022 (dollars in millions):",
      "context_summary": "Apple Inc. 10-K annual report for fiscal year 2024 ending September 28, 2024, covering financial statements, revenue by product segment (iPhone, Mac, iPad, Wearables), marketable securities, debt, in…",
      "score": 0.9928907155990601
    },
    {
      "chunk_id": 739,
      "document_id": 62,
      "page": 85,
      "text": "$\n416,161\n$\n391,035\n$\n383,285\nPortion of total net sales that was included in deferred revenue as of the beginning of the period\n$\n8,229\n$\n7,728\n$\n8,169\n(1)\nServices net sales include amortization of the deferred value of services bundled in the sales price of\ncertain products. The Company’s proportion of net sales by disaggregated revenue source was generally consistent for\neach reportable segment in Note 13, “Segment Information and Geographic Data” for 2025, 2024 and\n2023, except in Greater China, where iPhone revenue represented a moderately higher proportion of\nnet sales. As of September 27, 2025 and September 28, 2024, the Company had total deferred revenue of $\n13.7\nbillion and $\n12.8\nbillion, respectively. As of September 27, 2025, the Company expects\n66\n% of total deferred revenue to be realized in less than a year,\n23\n% within one-to-two years,\n9\n% within two-to-three years and\n2\n% in greater than three years. Note 3 –\nEarnings Per Share\nThe following table shows the computation of basic and diluted earnings per share for 2025, 2024 and\n2023 (net income in millions and shares in thousands):\n2025\n2024\n2023\nNumerator:",
      "context_summary": "Apple Inc. 10-K annual filing for fiscal year 2025 (ended September 27, 2025), covering financial statements, segment results by product lines, debt obligations, cash and investments, risk factors, a…",
      "score": 0.9890061616897583
    },
    {
      "chunk_id": 593,
      "document_id": 61,
      "page": 125,
      "text": "$\n74,200\nOperating income\n$\n27,082\n$\n30,328\n$\n31,153\nJapan:\nNet sales\n$\n25,052\n$\n24,257\n$\n25,977\nOperating income\n$\n12,454\n$\n11,888\n$\n12,257\nRest of Asia Pacific:\nNet sales\n$\n30,658\n$\n29,615\n$\n29,375\nOperating income\n$\n13,062\n$\n12,066\n$\n11,569",
      "context_summary": "Apple Inc. 10-K annual report for fiscal year 2024 ending September 28, 2024, covering financial statements, revenue by product segment (iPhone, Mac, iPad, Wearables), marketable securities, debt, in…",
      "score": 0.9879211187362671
    },
    {
      "chunk_id": 592,
      "document_id": 61,
      "page": 124,
      "text": "2024\n2023\n2022\nAmericas:\nNet sales\n$\n167,045\n$\n162,560\n$\n169,658\nOperating income\n$\n67,656\n$\n60,508\n$\n62,683\nEurope:\nNet sales\n$\n101,328\n$\n94,294\n$\n95,118\nOperating income\n$\n41,790\n$\n36,098\n$\n35,233\nGreater China:\nNet sales\n$\n66,952\n$\n72,559",
      "context_summary": "Apple Inc. 10-K annual report for fiscal year 2024 ending September 28, 2024, covering financial statements, revenue by product segment (iPhone, Mac, iPad, Wearables), marketable securities, debt, in…",
      "score": 0.9828665852546692
    }
  ],
  "detail": null
}
```

Note the citation quote (`"Total net sales\n$\n391,035"`) is verbatim from the actual
retrieved chunk text — `$391,035` million matches Apple's real reported FY2024 net
sales, and `validate_answer()` accepted it because the normalized quote is found
inside `chunk_id=514`'s text.

Running the pipeline against real documents is what surfaced every bug in
`INCIDENTS.md`, all three now fixed with regression tests:

| Incident | Symptom | Root cause |
|---|---|---|
| Fenced JSON | Every answer came back `null` | Model wrapped its JSON in a ` ```json ` fence despite the system prompt; `json.loads()` choked |
| Total DB outage | Plain-text `500`, breaking the JSON contract | `RetrievalDegraded` only guarded the dense branch — an outage kills the FTS branch it falls back *to* |
| Citation false-rejection | Correct answers discarded ~half the time | PDF extraction put `$` on its own line, so the gate rejected the model's natural `$391,035` |

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
scripts/             ingest CLI, migrate, fetch_corpus (demo corpus), dev_token (dev JWT)
```

## What I'd do next at 10x scale

- **Rate limiting** (`app/rate_limit.py`): per-user request limiting is an
  in-memory fixed-window counter, chosen because it's the cheap gate that
  runs before the org budget check's DB aggregate — no extra Postgres round
  trip per query. Ceiling: the counter is per-process state. Behind a
  load-balanced, multi-replica deployment each replica enforces its N
  req/min independently, so a user's effective limit becomes N × replica
  count, not N. Past one replica, move to a shared counter — a `rate_limit`
  table UPSERT-incremented on `(user_id, window_start)`, same pattern
  `token_usage.py` already uses — rather than reaching for Redis.
- **pgvector**: fine to ~10M chunks; past that, a dedicated vector DB (or
  sharding by org) to keep ANN index build/query time bounded.
- **Ingestion job queue**: Postgres `FOR UPDATE SKIP LOCKED` (`jobs.py`) is
  fine to ~tens of jobs/sec; past that, a real broker (SQS/Kafka) so a hot
  queue table doesn't become a lock-contention bottleneck.

## Status

- [ ] Phase 1 — Core: ingestion CLI, query path, 50-question golden set
- [ ] Phase 2 — Production layer: auth/tenancy, ingestion lifecycle, rate limits, fallbacks, CI gate
- [ ] Phase 3 — Evidence: METRICS.md numbers, Graph RAG experiment write-up, INCIDENTS.md

See `METRICS.md` and `INCIDENTS.md` — both must contain real measured output
before this project is called done.
