# Metrics

All numbers must be measured on this system, with hardware/config noted.
No estimates. Empty cells mean "not yet measured" — never fill with guesses.

**Measurement environment:** _(fill in: CPU/GPU, RAM, model versions, corpus size)_

## Retrieval quality (golden set, n = __)

| Configuration | Recall@5 | Recall@10 | MRR | p95 latency (ms) |
|---|---|---|---|---|
| Dense only (BGE-M3) | | | | |
| FTS only | | | | |
| Hybrid + RRF | | | | |
| Hybrid + RRF + rerank | | | | |

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
| | | | | |

## Graph RAG experiment (the documented cut)

_2-day Neo4j spike: relational-question subset of the golden set, vector-only vs
vector+graph. Record recall delta, added ops burden, and the keep/cut decision._
