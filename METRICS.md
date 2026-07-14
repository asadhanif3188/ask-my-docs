# Metrics

All numbers must be measured on this system, with hardware/config noted.
No estimates. Empty cells mean "not yet measured" — never fill with guesses.

**Measurement environment:** Intel i7-13700H (14C/20T), 15.7 GB RAM, no CUDA GPU
(embeddings and reranker run on CPU). Postgres 16 + pgvector in Docker. Corpus: 12
SEC 10-K filings (AAPL/MSFT/NVDA, FY2022–FY2026), 1,973 chunks, all embedded; 1
corrupt PDF correctly quarantined. Embeddings BAAI/bge-m3 (1024d), reranker
BAAI/bge-reranker-base, generator + Ragas judge claude-haiku-4-5-20251001.
Retrieval `top_k=50` → rerank `top_k=5`.

## Retrieval quality (golden set, n = 45 answerable)

Recall@5 here = "did an expected chunk survive into the 5 the model was shown".
Measured from the live eval run (`--dump`), not a separate harness.

| Configuration | Recall@5 | Recall@10 | MRR | p95 latency (ms) |
|---|---|---|---|---|
| Dense only (BGE-M3) | | | | |
| FTS only | | | | |
| Hybrid + RRF | | | | |
| Hybrid + RRF + rerank | **76%** (any expected chunk)<br>**69%** (all expected chunks) | | | |

Recall@5 by question kind — this is the finding, and it is why the golden set was
built with `kind` tags:

| Kind | n | Recall@5 (any expected chunk) |
|---|---|---|
| factual (FTS-friendly) | 20 | **20/20 = 100%** |
| semantic (paraphrase, dense branch) | 15 | **7/15 = 47%** |
| synthesis (2 chunks needed) | 10 | 7/10 = 70% |

Factual retrieval is solved; **paraphrased questions are where retrieval breaks.**
The semantic cases were written to share almost no vocabulary with their source
passage, so they can only be served by the dense branch — and the dense branch is
missing more than half of them. Note the ablation rows above are still empty: we do
not yet know whether the dense branch is weak on its own or whether RRF is letting
FTS drown it, and those are different bugs with different fixes. Measure before
touching anything.

## Contextual retrieval ablation

| Chunking | Recall@5 | Notes |
|---|---|---|
| Plain chunks | | |
| Contextual prefix (doc summary) | | |

## End-to-end query path

| Metric | p50 | p95 | p99 |
|---|---|---|---|
| Total latency (ms) | | | |
| Retrieval stage (ms) | | | |
| Rerank stage (ms) | | | |
| Generation stage (ms) | | | |
| Cost per query ($) | | | |

## Eval gate history

| Date | Golden set size | Faithfulness | Context recall | Answer relevance |
|---|---|---|---|---|
| 2026-07-14 (BASELINE) | 50 | 0.53 | 0.53 | 0.47 |
| 2026-07-14 (+ repair round) | 50 | **0.65** | **0.67** | **0.59** |

Gate thresholds are 0.85 / 0.80, so both runs **fail (exit 1)**. That was the
expected first result, and nothing was tuned in response to the baseline.

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
