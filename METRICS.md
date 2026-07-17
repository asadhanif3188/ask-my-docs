# Metrics

All numbers must be measured on this system, with hardware/config noted.
No estimates. Empty cells mean "not yet measured" — never fill with guesses.

**Measurement environment:** Intel i7-13700H (14C/20T), 15.7 GB RAM, no CUDA GPU
(embeddings and reranker run on CPU). Postgres 16 + pgvector in Docker. Corpus: 12
SEC 10-K filings (AAPL/MSFT/NVDA, FY2022–FY2026), 1,973 chunks, all embedded; 1
corrupt PDF correctly quarantined. Embeddings BAAI/bge-m3 (1024d), reranker
BAAI/bge-reranker-base, generator + Ragas judge claude-haiku-4-5-20251001.
Retrieval `top_k=50` → rerank `top_k=5`. Retrieval ablation, latency, cost, and the
current Ragas row measured 2026-07-17 (`run_retrieval_bench.py` + `run_evals.py`).

## Retrieval quality (golden set, n = 45 answerable)

Measured by `evals/run_retrieval_bench.py`, which runs every answerable golden case
through the four retrieval configurations and scores recall/MRR of the known source
chunk (`golden_set.jsonl` `chunk_ids`), reusing the production retrieval code paths
(`_fts_search`, `_dense_search`, `hybrid_retrieve`, `rerank`) — not a private harness.
Recall@5 (any) = "≥1 expected chunk survived into the 5 the model would be shown";
(all) = "every expected chunk survived". MRR = reciprocal rank of the first expected
chunk over the retrieved list (depth = `retrieve_top_k` = 50). Latency is
retrieval-stage only (excludes generation), timed one query at a time so the numbers
are single-query cost, not throughput. Reranker runs on CPU (no CUDA GPU — see caption).

Reproduce: `uv run python -m evals.run_retrieval_bench --dump bench.json`

| Configuration | Recall@5 (any) | Recall@5 (all) | Recall@10 (any) | MRR | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|---|
| Dense only (BGE-M3) | 75.6% | 71.1% | 75.6% | 0.571 | 445 | 631 |
| FTS only | 2.2% | 2.2% | 2.2% | 0.022 | 5 | 15 |
| Hybrid + RRF | 77.8% | 73.3% | 77.8% | 0.581 | 446 | 623 |
| Hybrid + RRF + rerank (shipped) | 75.6% | 68.9% | **80.0%** | **0.670** | **35,950** | **40,569** |

Recall@5 (any expected chunk) by question kind — the ablation, per kind:

| Kind | n | Dense | FTS | Hybrid + RRF | + rerank (shipped) |
|---|---|---|---|---|---|
| factual | 20 | 90.0% | 5.0% | 95.0% | **100.0%** |
| semantic (paraphrase) | 15 | 53.3% | 0.0% | 53.3% | 46.7% |
| synthesis (2 chunks needed) | 10 | 80.0% | 0.0% | 80.0% | 70.0% |

**Interpretation — three measured results, one of which overturns the earlier story:**

1. **FTS is dead weight, not the factual workhorse.** An earlier draft of this file
   asserted "factual retrieval is solved because factual queries lean on FTS." The
   ablation says the opposite: FTS-only scores **2.2% recall@5 (1/45), 5% on factual.**
   `websearch_to_tsquery('english', <full question>)` ANDs every content word, and no
   single ~500-token chunk contains all ~15 words of a natural-language question, so
   FTS returns almost nothing rankable. **Dense (BGE-M3) carries retrieval on its own:**
   dense 75.6% → hybrid 77.8% is a +2.2 pp lift, essentially one factual case
   (factual 90% → 95%). RRF is fusing a strong dense branch with a near-empty FTS branch.

2. **The reranker is a bad trade on this hardware.** It improves ranking precision —
   MRR 0.581 → 0.670, factual recall@5 95% → 100%, recall@10 77.8% → 80.0% — but it
   *lowers* overall recall@5 (77.8% → 75.6%) and hurts exactly the kinds retrieval is
   already weakest on: semantic 53.3% → 46.7%, synthesis 80% → 70% (synthesis
   recall@5-all collapses 60% → 40%). It reorders the fused 50 and demotes correct
   paraphrase/multi-chunk passages below the top 5. And it costs **~35,500 ms p50 /
   ~40,000 ms p95** — the cross-encoder scores all 50 candidates on CPU with no GPU. Net:
   **the reranker added ~35 s per query for −2.2 pp recall@5**, buying only MRR and
   factual precision. On a GPU (~200 ms) it would be an easy keep; on this CPU box it is
   the single largest latency cost in the system and a live removal candidate — the
   honest call is "keep it for factual/MRR precision only if it can be moved off CPU."

3. **Paraphrase is still the wall, and no config here clears it.** Semantic recall@5
   tops out at 53.3% (dense/hybrid) and the reranker makes it worse. This is the same
   hole every prior measurement found; it is a dense-embedding-quality problem, not a
   fusion or ranking one, so it needs a stronger embedder or query-side expansion — see
   the query-rewrite ablation below for why the rewrite we built could not move it.

These numbers corroborate the two figures the earlier live-eval run produced for the
shipped config (recall@5 any 75.6% ≈ "76%", all 68.9% ≈ "69%", per-kind factual 100% /
semantic 7/15 / synthesis 7/10) — the benchmark reproduces them exactly and fills in
the ablation rows that were blank.

## Contextual retrieval ablation

| Chunking | Recall@5 | Notes |
|---|---|---|
| Plain chunks | | |
| Contextual prefix (doc summary) | | |

## End-to-end query path

### Retrieval-path latency (ms, retrieval stage only; from `run_retrieval_bench.py`)

| Stage | p50 | p95 | p99 |
|---|---|---|---|
| Retrieval — hybrid (embed + 2 SQL + RRF) | 446 | 623 | 682 |
| Retrieval — dense only (embed + SQL) | 445 | 631 | — |
| Retrieval — FTS only (SQL) | 5 | 15 | — |
| Rerank — cross-encoder, 50 candidates, **CPU** | 35,422 | 40,106 | 40,844 |
| Generation stage | _not instrumented_ | | |
| Total end-to-end | _not instrumented_ | | |

Generation- and total-latency instrumentation is Project 3 (observability) work and is
not measured here — left blank rather than estimated. What the retrieval numbers already
show: **the reranker, not generation, dominates wall-clock.** Hybrid retrieval is ~0.45 s;
the CPU cross-encoder adds ~35 s on top, so a shipped query cannot return in under ~36 s
p50 on this hardware regardless of how fast the LLM answers. The BGE-M3 query embedding is
the bulk of the ~0.45 s retrieval cost (FTS SQL alone is 5 ms).

### Cost per query (measured, not estimated)

From `token_usage` rows recorded by the live eval run on 2026-07-17 (org 54,
`purpose`-grained; query the table, don't re-estimate). Model **claude-haiku-4-5** for
generation, repair, and the doc-summary call. Pricing **$1.00 / MTok input,
$5.00 / MTok output** — Anthropic pricing page (platform.claude.com/docs/en/pricing),
retrieved 2026-07-17. Ragas judge cost is **excluded** (eval infrastructure, not product);
`token_usage` only records the system-under-test's own calls. Rewrite is off, so it makes
no calls and records nothing.

Reproduce: `uv run python -m evals.run_evals --dump rows.json`, then
`SELECT purpose, input_tokens, output_tokens FROM token_usage WHERE org_id=54 AND day='2026-07-17';`

| Purpose | calls | mean input tok | mean output tok | $/call | total $ (this run) |
|---|---|---|---|---|---|
| generation | 49 | 2,177 | 138 | $0.00287 | $0.14057 |
| repair round | 10 (20.4% of queries) | 2,585 | 187 | $0.00352 | $0.03522 |
| query rewrite | 0 (feature off) | — | — | $0 | $0 |
| **Blended cost / query** (÷ 50 golden) | | | | **≈ $0.0035** | $0.17579 total |

**Repair-round frequency: 20.4% (10 of the 49 queries that reached generation) needed a
one-shot repair round, at ≈ $0.0035 each.** Of those 10 repairs, 9 recovered a valid
answer and 1 still blocked. Amortized, the repair round adds ≈ $0.0007 to the mean query
(20.4% × $0.00352) — cheap insurance that buys back 9 answers (see the repair delta
below). Generation is where the money is: 2,177 input tokens/query is the ~5-chunk context
(`retrieve_top_k` → `rerank_top_k` = 5 passages plus their context summaries); output is
tiny (138 tok) because answers are terse cited JSON. At Haiku prices the whole product path
is ~$0.0035/query; the same context on Sonnet 5 ($3/$15) would be ~5× that, on Opus 4.8
($5/$25) ~9× — the cheap-tier choice is what keeps per-query cost in fractions of a cent.

_(1 of the 50 golden cases — g003 — hit the degraded-retrieval fallback under concurrency
this run: the dense branch blipped, FTS-only returned nothing (see the 2.2% FTS row above),
and the case ended `no_context` without calling generation. Hence 49 generation calls, not
50. That is the degraded path behaving as designed, recorded honestly, not a dropped case.)_

## Eval gate history

| Date | Golden set size | Faithfulness | Context recall | Answer relevance | Answered (of 45) |
|---|---|---|---|---|---|
| 2026-07-14 (BASELINE) | 50 | 0.53 | 0.53 | 0.47 | 24 |
| 2026-07-14 (+ repair round) | 50 | 0.65 | 0.67 | 0.59 | 30 |
| 2026-07-17 (current) | 50 | **0.67** | **0.69** | **0.61** | **32** |

Reproduce the current row: `uv run python -m evals.run_evals` (full 50-case run,
`claude-haiku-4-5` generator + Ragas judge).

Gate thresholds are 0.85 / 0.80, so all three runs **fail (exit 1)** — as expected while
retrieval, not generation, is the ceiling. The current run is marginally above the
+repair baseline (faithfulness 0.65 → 0.67, recall 0.67 → 0.69, 30 → 32 answered); the
deltas are within Ragas judge + rerank-ordering run-to-run noise, not a tuning gain — no
retrieval change was made between them. **The hallucination gate held: 0/5 unanswerable
cases answered.**

Per-kind (current run), which localizes exactly where the aggregate is lost:

| Kind | n | Answered | Faithfulness | Answer relevance | Context recall |
|---|---|---|---|---|---|
| factual | 20 | 18 | 0.90 | 0.81 | 0.90 |
| semantic (paraphrase) | 15 | 8 | 0.49 | 0.40 | 0.47 |
| synthesis | 10 | 6 | 0.48 | 0.54 | 0.60 |

The story is unchanged and consistent with the retrieval ablation above: **factual is
near-solved (0.90 across the board), semantic is where the system bleeds** (only 8/15
answered, faithfulness 0.49 — because context recall is 0.47, the model has nothing to be
faithful *to*). Answered-count follows retrieval recall almost exactly (semantic 8/15
answered vs 46.7% recall@5; synthesis 6/10 vs 70%). Generation faithfulness is not the
problem — every answered case that had the right chunk scored well; the aggregate is
dragged down by cases where the right chunk never arrived, scored 0 and kept in the
denominator (dropping them would report a false 0.9+).

### Delta: the one-shot repair round (Prompt 1.6)

The only change between those two rows is `generate.py`: an answer the validator
rejects is shown its own output plus the specific rejection reason, and gets exactly
one more attempt. Same gates apply to the retry.

| | baseline | + repair | delta |
|---|---|---|---|
| Answered (of 45 answerable) | 24 | **30** | **+6** |
| Faithfulness | 0.53 | 0.65 | +0.12 |
| Context recall | 0.53 | 0.67 | +0.14 |
| Answer relevancy | 0.47 | 0.59 | +0.12 |
| Hallucinated on unanswerable | 0/5 | **0/5** | unchanged |

Measured directly on the 13 cases the gates had blocked: **8 now answer**, 4 became
clean refusals, 1 still blocks. Every gain is a case where the model *had* the right
chunk and fumbled the output format — a quote it retyped instead of copying, a
figure it computed, a response that wasn't JSON.

Two results in that table matter more than the headline:

**The 4 that became refusals are a win, not a loss.** All four (g029, g030, g032,
g033) are retrieval misses — the expected chunk was never in the top 5. The repair
prompt states that `{"claims":[]}` is a valid answer, so instead of straining to
answer from chunks that cannot support one and getting caught by the validator, the
model now declines. Same outcome for the user, reached honestly and one gate earlier.

**The hallucination count did not move.** A repair round is exactly the kind of
change that could quietly become a bypass — "the model tried twice, ship it". It
did not, because the repaired output runs through the identical
`validate_answer` + `verify_entailment` path. Still 0/5 on the unanswerable cases.

Not fixed by the repair, and worth being precise about: **retrieval is now the whole
story.** Semantic recall@5 is unchanged at 47% — no prompt can repair a chunk that
was never retrieved. The remaining silent cases are almost entirely retrieval misses,
which is where the next work belongs.

### What the baseline actually says

The headline number is misleading in a way worth stating plainly, because it points
at the wrong fix.

**The system never lied. It went quiet.** Every case that produced an answer scored
**faithfulness 1.00 and context_recall 1.00** — 24 of them. The aggregate is 0.53
purely because the other 21 answerable cases produced *no* answer and are scored 0
(dropping them would report a triumphant 1.00 on the questions we happened to
answer, which is how eval suites lie). And the hallucination gate held perfectly:
**0 of 5 unanswerable questions were answered** — all 5 correctly refused, including
the Azure-revenue trap where retrieval does surface a plausible-looking Azure chunk
that simply does not contain the figure.

So the baseline is not "the answers are bad". It is **"the answers are absent"**, in
two distinct families:

| Family | n | Cause |
|---|---|---|
| **Retrieval miss** → model correctly refused | 8 | The expected chunk never reached the model (7 of 8 had no expected chunk in the top 5). Concentrated in semantic/synthesis. Refusing on bad context is *correct behaviour* — the bug is upstream, in recall. |
| **Blocked by our own gates** | 13 | The model answered and the answer was rejected before serving. Breakdown below. |

Blocked cases, by true cause (the 5 unanswerable cases are excluded — those refusals
are correct):

| Cause | n | Cases |
|---|---|---|
| `validate.py` number gate: claim asserts a figure not present in the cited chunks | 4 | g036, g038, g040, g043 |
| `validate.py`: quote not verbatim in the cited chunk | 3 | g016, g021, g029 |
| Generator returned non-JSON | 3 | g030, g032, g033 |
| Entailment judge: claim not entailed by its evidence | 2 | g022, g039 |
| Entailment judge returned unparseable output (judge-side bug) | 1 | g013 |

Three of these are worth calling out:

1. **The number gate is blocking correct arithmetic.** All 4 of its rejections
   (g036, g038, g040, g043) are the *derived-arithmetic* synthesis cases flagged
   during golden-set review — e.g. "R&D grew $8,299M", where 34,550 and 26,251 are
   both in the chunks but 8,299 is not, because the model computed it. `validate.py`
   is deterministic and cannot tell a *derived* figure from a *fabricated* one, so it
   rejects both. This is the gate working as designed and the design having a real
   cost. It is a genuine tension, not a bug to paper over: loosening it to allow
   arithmetic is exactly how a fabricated figure gets served.

2. **3 non-JSON generator outputs** are precisely what the repair round (Prompt 1.6)
   exists to catch. That is ~7% of answerable cases lost to a retry we have not
   implemented yet.

3. **The entailment judge itself failed once** (g013) with unparseable output. The
   judge fails closed — an unverified claim is not served — so this cost an answer
   rather than serving a lie, which is the right direction to fail in. But a judge
   that returns garbage is its own bug.

**Do not read this table as a ranking of what to fix.** The retrieval misses (8) and
the semantic recall hole (47%) are one problem; the 13 blocks are four unrelated
problems sharing a symptom. They should be attacked separately, each with its own
before/after number in this file.

### Context pollution (measured from the same run)

| Metric | Value |
|---|---|
| XBRL tag-soup chunks in corpus | 379 / 1,973 (19%) |
| Top-5 context slots shown to the model that were tag soup | **54 / 250 (21.6%)** |
| Questions with ≥1 tag-soup chunk in their top 5 | **19 / 50** |
| Retrieval-miss refusals that had tag soup in their top 5 | 6 / 8 |

One in five of the model's context slots is unreadable XBRL metadata (see
INCIDENTS.md). It is *correlated* with the failures, not yet proven to cause them —
the semantic cases are independently hard for the dense branch, and both effects land
on the same questions. The disentangling experiment (filter tag soup at query time,
re-measure recall@5) needs no re-ingest and should run before any ingestion change.

## Query rewrite ablation (the documented cut)

Phase 1's query path specifies rewrite -> hybrid retrieve -> RRF -> rerank ->
generate. Implemented in `app/retrieval/rewrite.py`: one `summary_model` call per
query, strict JSON out, producing a DENSE form (pronouns resolved, abbreviations
expanded, synonyms added) and an FTS form (filler stripped, identifiers — tickers,
numbers, dates — preserved verbatim). Any failure falls back to the raw question for
both branches; wired behind `settings.enable_rewrite` specifically so this
measurement is a config flip, not a code change.

Measured on the same golden set and harness as the recall table above
(`evals/run_evals.py --dump`), rewrite ON vs OFF, n=45 answerable cases:

| Kind | OFF (baseline) | ON (rewrite) | Delta |
|---|---|---|---|
| factual (FTS-friendly) | 20/20 = 100% | 19/20 = 95% | **-5pp** |
| semantic (paraphrase, dense branch) | 7/15 = 47% | 7/15 = 47% | **0** |
| synthesis (2 chunks needed) | 7/10 = 70% | 7/10 = 70% | **0** |
| **Total recall@5 (any expected chunk)** | 34/45 = 75.6% | 33/45 = 73.3% | **-2.2pp** |

Added latency — standalone `rewrite_query()` timing over the same 45 questions,
concurrency=4, zero fallbacks (no API errors): **p50 2,192ms, mean 3,087ms, p95
6,069ms** per query, on top of retrieval + rerank + generation.

**Verdict: cut — no measurable gain — flag left in place (default off), documented.**

The retrieval ablation above now explains *why* the rewrite could not help, beyond the
paraphrase result: half of what the rewrite does — strip filler from the FTS form — targets
a branch that contributes **2.2% recall@5**. Improving the FTS query cannot move a fused
result that is ~entirely dense-driven. The other half — expand/resolve the dense form — is
the lever that *could* matter, and it did nothing on the 15 semantic cases (7/15 both ways).
So the feature was cut on measurement, and the ablation says a *better* dense-form rewrite
(or a stronger embedder) is the only thing in this area worth re-measuring.

The semantic/paraphrase cases were the entire hypothesis for this feature — "resolve
pronouns, expand abbreviations, add synonyms" should help exactly the dense-branch
cases BGE-M3 struggles with — and recall@5 there did not move: 7/15 both ways, the
same seven case IDs answered either way. Synthesis was likewise unchanged. Whatever
is limiting the dense branch on paraphrased questions, it is not query phrasing that
a rewrite step can fix.

Worse, rewrite cost a factual case it should never have touched: g019 ("expiration
dates of NVIDIA's currently issued patents") lost its one expected chunk (id 8428)
from the top 5 under rewrite — the rewritten FTS form pulled in five unrelated
chunks instead of the exact match the raw question found. The prompt explicitly
instructs the FTS form to preserve identifiers verbatim; this case shows that
instruction alone doesn't prevent an exact-match regression, only a *literal*
find-and-replace of a named identifier — the phrasing around it can still shift
which chunks a `websearch_to_tsquery` match ranks first.

`enable_rewrite` stays wired and defaults to **off** by this finding, so a future
retrieval change (e.g. a stronger dense model, or a rewrite prompt scoped to only
the semantic-branch failure mode) can be re-measured against it without
re-implementing the feature.

## Graph RAG experiment (the documented cut)

_2-day Neo4j spike: relational-question subset of the golden set, vector-only vs
vector+graph. Record recall delta, added ops burden, and the keep/cut decision._
