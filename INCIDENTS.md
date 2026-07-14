# Incidents

Real failures debugged in this system. Each entry must include: symptom → how
traces/logs led to the cause → fix → the regression test that now prevents it.
At least one entry is required before this project is called done.

---

## Template

**Date:**
**Symptom:** _(what the user/eval saw)_
**Detection:** _(which metric, trace, or test surfaced it)_
**Root cause:** _(the actual mechanism, not the category)_
**Fix:** _(commit/PR link)_
**Regression guard:** _(test file :: test name)_
**Time to resolve:**

---

## Incident: LLM wraps JSON output in a markdown code fence, breaking citation validation

**Date:** 2026-07-13
**Symptom:** The first real end-to-end `/query` call against the live corpus (12 real
SEC 10-K filings, real Anthropic API key) returned `"answer": null` with
`"detail": "Sources found but answer generation is temporarily unavailable"` —
despite retrieval and reranking working perfectly (five highly relevant, real
Apple revenue chunks came back with reranker scores > 0.98).
**Detection:** Manually reproduced the exact retrieval → rerank → generate call
outside the API to capture the raw model output. `_call_llm` returned:
```
```json
{ "claims": [...] }
```
```
i.e. the model wrapped its JSON in a markdown fence despite `SYSTEM_PROMPT`
explicitly saying "Return ONLY JSON". `json.loads()` on that raw string fails
with `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`, which
`generate_answer()`'s broad except clause correctly caught and converted into
`GenerationUnavailable` — so the *fallback contract* worked exactly as designed
(sources still returned, no 500), but the root cause (fence-wrapped JSON) was
silently swallowed with no server-side log to point at it.
**Root cause:** Claude routinely fences JSON responses in ` ```json ` blocks
regardless of a system-prompt instruction not to; `generate_answer()` called
`json.loads(raw)` directly with no tolerance for that formatting.
**Fix:** Added `_extract_json()` in `app/generation/generate.py`, applied before
`json.loads()`. First attempt was a fence-anchored regex, but code review pointed
out (and empirical testing confirmed) that a model already ignoring "Return ONLY
JSON" is just as likely to add `Here is the JSON:` before the fence or `Let me
know if...` after it — both of which the anchored regex silently failed to strip,
reproducing the identical bug. Final fix scans for the first balanced `{...}`
object via `json.JSONDecoder().raw_decode()`, which handles fenced, unfenced,
tagged, untagged, and prose-wrapped responses uniformly. Returns text unchanged
when no object is found, so a genuinely different parse failure still surfaces
normally.
**Regression guard:** `tests/test_generate.py` — both `_extract_json()` and
`generate_answer()` are parametrized over 7 real response shapes (plain,
```json fence, bare fence, fence + trailing prose, fence + leading prose, prose
on both sides, CRLF fence).
**Time to resolve:** ~15 minutes to diagnose and fix the observed shape;
another pass to generalize the fix after review flagged the anchoring gap.

---

## Incident: total DB outage isn't covered by the "degraded" fallback contract

**Date:** 2026-07-13
**Symptom:** Stopped the compose Postgres container mid-session, then sent a
`/query` request. Got back `HTTP 500` with body `Internal Server Error`
(`content-type: text/plain`) after ~4.7s — not a stack trace (good, nothing
leaked to the client), but also not the structured JSON the rest of the API
always returns (`QueryResponse` shape), and not the "degraded" response the
architecture appears designed to produce for exactly this kind of failure.
**Detection:** Server-side log showed the real cause: an unhandled
`ConnectionRefusedError` propagating up from `asyncpg` through
`hybrid.hybrid_retrieve` → `app/api/query.py`, caught only by Starlette's
default `ServerErrorMiddleware` (no custom exception handler is registered in
`app/main.py`).
**Root cause:** `hybrid_retrieve()`'s `RetrievalDegraded` fallback only wraps
the *dense* search call:
```python
fts_results = await _fts_search(org_id, question, top_k)   # unprotected
try:
    dense_results = await _dense_search(org_id, question, top_k)
except Exception:
    raise RetrievalDegraded(fallback_results=fts_results[:top_k])
```
It assumes Postgres itself is always reachable and only the *embedding step*
can fail. A full DB outage fails the FTS call too — the one call the fallback
path has no answer for — so it isn't "degraded," it's unhandled. Separately,
there's no global FastAPI exception handler, so *any* unhandled exception
anywhere in the app (not just this one) falls through to Starlette's generic
plain-text 500 rather than a JSON error consistent with the rest of the API.
**Fix:** Three changes, driven by the decision that a total DB outage should be
a **503, not a 200** — a 200 with `answer: null` would tell clients and
monitoring "search worked, nothing matched", which is a lie that suppresses
retries and alerting. (This is deliberately *unlike* the LLM-down case, which
stays 200 because we still have real retrieved sources worth returning.)
1. `RetrievalUnavailable` in `app/retrieval/hybrid.py`, raised when the FTS
   branch — the thing `RetrievalDegraded` falls back *to* — is itself down.
2. `app/api/query.py` catches it and returns `503 {"detail": "Search backend
   unavailable, please retry"}`. The exception carries the DSN, so the client
   detail is deliberately generic; the real cause is logged server-side.
3. `app/main.py` gained a global `Exception` handler so *no* route can fall
   through to Starlette's plain-text 500 and break the API's JSON contract;
   `/healthz` now returns `503 {"status": "unavailable"}` so orchestrators pull
   the pod from rotation instead of seeing an opaque 500.
**Regression guard:** `tests/test_db_outage.py` — 5 tests covering the
`RetrievalUnavailable` raise, the 503 JSON body, that the body leaks neither DSN
nor traceback, the `/healthz` 503, and that an unhandled exception still yields
JSON rather than plain text.
**Verified live:** stopped the compose Postgres and replayed the same request —
`/healthz` → `503 {"status":"unavailable"}`, `/query` → `503 {"detail":"Search
backend unavailable, please retry"}` (both `application/json`, previously
plain-text 500). Restarted Postgres: both recovered with no API restart.
**Time to resolve:** ~20 minutes.

---

## Incident: citation gate false-rejects correct answers over PDF whitespace artifacts

**Date:** 2026-07-13
**Symptom:** After the DB-outage fix, replaying the *same* Apple revenue query
that had previously succeeded returned `200` with `answer: null` and
`"detail": "Sources found but answer generation is temporarily unavailable"` —
despite retrieval returning the same five correct chunks. The answer was not
"unavailable" at all: the model produced a correct, correctly-cited answer and
the hallucination gate threw it away.
**Detection:** The server log said nothing, because `app/api/query.py` catches
`GenerationUnavailable` and returns the fallback *without logging the reason* —
the same blind spot that made the earlier code-fence incident slow to diagnose.
Reproducing the call directly surfaced it:
`CitationValidationError: Quote not found verbatim in chunk 514: 'Total net sales $391,035'`.
**Root cause:** `validate._normalize()` collapses whitespace *runs* into a single
space but does not remove whitespace. The chunk, as extracted from the PDF, reads
`Total net sales\n$\n391,035` (the `$` lands on its own line) and normalizes to
`total net sales $ 391,035` — with a space between `$` and the number. The model
wrote the same span naturally as `$391,035`, which normalizes to
`total net sales $391,035`. Identical content, one whitespace character apart,
so the substring check fails. Whether a citation validates therefore depends on
whether the model happens to mirror an artifact of PDF text extraction — an
effective coin-flip on numeric citations, which are exactly the citations that
matter most for a financial-filing corpus.
**The fix that was wrong (worth recording).** First attempt made `_normalize()`
drop whitespace entirely (`"".join(text.split())`), reasoning that the model
still could not introduce characters absent from the chunk. That reasoning was
wrong, and an adversarial security review — plus a direct check against the
project's own test fixture — proved it. In a financial table the newline **is
the cell delimiter**: pypdf flattens `391,035 | 2 | %` into
`391,035\n2\n%`. Strip the whitespace and the cells fuse, so the gate began
accepting quotes for figures that never existed:

| Quote | Before | After blanket strip |
|---|---|---|
| `391,0352` (sales figure fused with an adjacent footnote marker) | blocked | **accepted** |
| `391,035 2%` / `2%` (footnote spliced into another column's `%`, inventing a growth rate) | blocked | **accepted** |

A model could cite `"391,035 2%"` — verbatim, in order, every character present —
and assert *"net sales grew 2%"*, a number appearing nowhere in the filing. The
relaxation broke the exact guarantee the service exists to provide, in exactly
the domain it targets.

**Fix (shipped).** Two changes, both scoped to symbol→digit and *never*
digit→digit:
1. `app/ingestion/pipeline.clean_extracted_text()` rejoins a currency/open-paren
   symbol with the digits that follow it at extraction time, so chunks store what
   the filing actually renders. Root cause, fixed where it is created.
2. `validate._normalize()` collapses whitespace runs (as before) and additionally
   folds whitespace between `$`/`(` and a following digit. Digit-to-digit
   whitespace is preserved, so cell boundaries survive and every splice above
   stays blocked.

**Paired fix:** `app/api/query.py` now logs the `GenerationUnavailable` cause.
That branch swallows everything from a dead upstream to a rejected citation while
telling the user only "temporarily unavailable" — so a bug in *our own validator*
was indistinguishable from an Anthropic outage. That silence is why this incident
and the code-fence one both cost a manual reproduction hunt.
**Regression guard:** `tests/test_citation_validation.py` — the original failure
(`$391,035` vs `$\n391,035`), the inverse (model mirrors the artifact), and a
parametrized `test_cross_cell_splice_is_blocked` pinning all four fabrications
above as rejected. `tests/test_ingestion_lifecycle.py` pins that the extraction
repair never fuses two figures. `test_generation_failure_reason_is_logged` pins
the logging.
**Verified live:** replayed the failing query against the real corpus 3× — 3/3
returned a validated answer citing `Total net sales $391,035`, the exact quote
form previously rejected. Before the fix the same query alternated between a
correct answer and `answer: null` depending on how the model rendered whitespace.
**Follow-up (closed):** the `Claim.text` gap below.
**Time to resolve:** ~40 minutes, nearly all of it spent getting the *scope* of
the relaxation right. The one-line version was fast and wrong.

---

## Incident: the gate checked the quote, never the sentence the user reads

**Date:** 2026-07-13
**Symptom:** No user-visible failure — which is what makes it worth recording.
Found during an adversarial review of the citation gate: `validate_answer()`
verified `citation.quote` against the chunk, but never `Claim.text`. The claim
sentence is free-form model output and is *the thing the user actually reads*.
So this passed the gate cleanly:

```
claim.text  = "Revenue COLLAPSED by 90% to just $4.2 million, a catastrophic decline."
citation.quote = "Total revenue for fiscal 2024 was $4.2 billion"   # verbatim, correctly cited
```

A flawless, correctly-attributed citation hung under a sentence that inverts its
meaning, invents a 90% figure, and shifts the magnitude by three orders. "The
quote checks out" was never the same as "the answer is true," and the product
sold the former as the latter.
**Detection:** Adversarial security review of the whitespace fix above, which
asked what the gate does *not* check. Not a user report and not a test failure —
no test existed that could fail.
**Root cause:** The citation contract was designed around attribution
(*does this quote exist in a retrieved chunk?*) and silently assumed faithfulness
(*does the sentence follow from the quote?*) came with it. It does not.
**Fix:** Two layers, because they fail differently.
1. **Numeric grounding** (`validate.py`, deterministic, free): every significant
   figure in `claim.text` must appear in the cited chunks (plus their context
   summaries — a claim legitimately takes the fiscal year from the document, not
   the table cell). For a financial corpus the invented *number* is the
   catastrophic failure, so numbers get a hard check that cannot be talked out of
   it. Single digits are excluded deliberately: they are everywhere in a filing,
   and demanding they match would resurrect the false-rejection bug above.
2. **Entailment** (`entailment.py`, LLM judge on the cheap tier): catches the
   misstatements that invent no number at all — a reversed direction, a swapped
   unit. Honest about what it is: an LLM checking an LLM, probabilistic, and the
   weaker layer by design, which is why the deterministic gate runs first. It
   **fails closed** — an unreachable or malformed judge means the claim is
   *unverified*, and unverified financial claims are not served as fact.
**Regression guard:** `tests/test_citation_validation.py` (invented figure
blocked despite a verbatim quote; real pipeline claims still pass — the false
rejection must not come back) and `tests/test_entailment.py` (reversed direction,
swapped unit, fail-closed on a dead judge, fail-closed on malformed judge output).
**Time to resolve:** ~1 hour.

---

## Incident: bullet glyphs arrive as control characters, and the validator normalized in the wrong order

**Date:** 2026-07-13
**Symptom:** With the faithfulness layers live, a real query — *"What are
Microsoft's main risk factors?"* — returned `answer: null`. Two other queries
(Apple, NVIDIA) were fine, so it was not a broad regression.
**Detection:** The `GenerationUnavailable` logging added in the incident above
paid for itself immediately: the log named the cause instead of staying silent.
It was **not** the new entailment judge, as first suspected — it was the verbatim
quote check. But the log truncated the quote to 80 characters, and the divergence
was 75 characters in, so it still took a manual reproduction to see. The truncation
is now removed; an error that hides the thing it is reporting is not an error message.
**Root cause:** Two bugs, stacked.
1. pypdf renders symbol-font bullets as **control characters** — the corpus holds
   800 DELs (`\x7f`) standing in for "•". The model omits them when quoting a
   bulleted passage, so a faithful quote failed the verbatim check. Same class as
   the whitespace artifact above, third costume.
2. Fixing (1) exposed a worse one: `_normalize()` **collapsed whitespace before
   cleaning**. Cleaning turns `\x7f` into a space, so collapsing first left a
   fresh run of spaces that nothing re-collapsed — chunk read `things:␣␣␣the`,
   quote read `things:␣the`, no match. The unit test passed only because it
   happened to clean-then-collapse, the correct order, while production did the
   reverse. A test that exercises the right order while the code runs the wrong
   one is worse than no test.
**Fix:** Control characters become a **space** (never nothing — deleting them
could fuse the tokens either side, the exact mistake this module exists to avoid),
and `_normalize()` now cleans *then* collapses, the same order ingestion uses.

**Then the process fix, which mattered more than the bug.** This was the *third*
artifact of the same family found one at a time — `$\n391,035`, then
`$\n(4,638\n)`, then `\x7f` — each one triggering its own ~20-minute re-ingest.
Chasing them individually was the actual mistake. So the corpus was finally
**audited in full**, up front, and the rest of the family fell out at once:

| Artifact | Count | Why the gate rejected a faithful quote |
|---|---|---|
| `’ ‘` curly quotes | 2,290 | the model types the ASCII `'` |
| `“ ”` curly doubles | 1,376 | the model types the ASCII `"` |
| `\x7f` bullets | 762 | invisible; the model omits it |
| `— –` dashes | 526 | the model types a hyphen |
| `■` checkbox glyph | 248 | the model omits it |
| `€` stranded from digits | 11 | same bug as `$`, different currency |

Measured against the real corpus: of 400 model-style quotes containing any of
these, **33/400 (8%) validated before the audit; 400/400 (100%) after.** Roughly
every other passage in a 10-K carries a curly apostrophe, so this was quietly
rejecting a large share of correct answers.

`text_norm` therefore does not *repair* text, it **canonicalises** it — and it runs
on both the chunk and the quote, so the filing's typography and the model's ASCII
fold to the same string and either validates.
**Consequence worth stating plainly:** none of this needed a re-ingest. Because
both sides are canonicalised at comparison time, a quote validates against a chunk
written by *any* pipeline version. Re-ingestion only buys cleaner embeddings and
cleaner prompt text — it is a quality improvement, not a correctness requirement,
and treating it as the latter cost three unnecessary passes over the corpus.
**Regression guard:** `tests/test_text_norm.py` (control char to space; never
fuses digit groups) and
`test_quote_validates_against_a_chunk_still_holding_a_bullet_control_char`, which
asserts against a chunk carrying a *raw* `\x7f` — pinning the ordering bug that
the clean-input test could not catch.
**Note for the next person:** re-ingesting is **not** required to fix this. The
validator cleans both sides at comparison time, so a quote validates against a
chunk whether or not that chunk has been reprocessed. Re-ingestion only buys
cleaner embeddings and cleaner prompt text. This matters because chasing a
re-ingest is what nearly hid bug (2): fresh data would have masked it while
leaving it live for every chunk written by an older pipeline version.
**Time to resolve:** ~30 minutes.

---

## Incident (OPEN): XBRL tag soup is embedded and indexed as retrievable content

**Date:** 2026-07-14
**Status:** OPEN — found while building the golden set; not yet fixed. Recorded now
so it is not rediscovered from scratch later.
**Symptom:** No user-visible failure yet. Sampling chunks to draft golden-set
questions, the first "long" chunk pulled from `NVDA_10K_2025-02-26.pdf` was not
prose but the filing's embedded XBRL metadata:

```
nvda-20230129 0001045810 2023 FY false P3Y P4Y P5Y
http://fasb.org/us-gaap/2022#AccruedLiabilitiesCurrent
nvda:AartiShahSeptember27202410b51TradingArrangementMember
```

**Detection:** A prose filter written to *sample* chunks (not to test anything)
rejected far more than expected. Measuring the corpus directly:
**379 of 1,973 chunks (19%) are XBRL tag soup**, 376 of them almost entirely so —
MSFT 185, NVDA 112, AAPL 82. Every one carries an embedding and a `tsv` entry, so
each is a live candidate in both retrieval branches.

Note the number that is *not* the story: ~44% of chunks fail a strict prose test,
but most of that is legitimate flattened financial tables (the revenue rows the
factual eval cases depend on). Conflating the two would have condemned the tables
along with the junk. The junk is the 19%.

**Root cause (hypothesised, unconfirmed):** `pdfplumber` text extraction returns the
inline XBRL / iXBRL fact tags that modern SEC filings embed in the document, and
the chunker treats that output as ordinary text. There is no content filter between
extraction and chunking — `pipeline.py` chunks whatever comes back.

**Impact:** These chunks cannot answer any question, but they can still be *retrieved*.
They are dense number-and-identifier strings, which is exactly the shape that scores
spuriously on FTS for queries containing dates, CIKs, or alphanumeric identifiers —
the same lexical branch the `kind: "factual"` eval cases are meant to exercise. Cost
is paid too: 379 needless embeddings per full ingest, and prompt tokens whenever one
survives reranking. **The hallucination gate is not at risk** — a model cannot
fabricate a citable verbatim quote out of tag soup, and `validate.py` would reject it
— so this degrades retrieval quality and cost, not correctness.

**Fix:** not yet written. Likely a content filter at ingest (drop chunks whose
tag-token density exceeds a threshold), which would require a re-ingest to take
effect for existing rows — the one thing this project has already learned to be
expensive and to treat with suspicion (see the incident above: a re-ingest almost
masked a live bug).

**Regression guard:** none yet. When fixed, the guard must pin a *real* tag-soup
chunk (chunk 8065 is a good specimen) rather than a synthetic string, for the same
reason `test_quote_validates_against_a_chunk_still_holding_a_bullet_control_char`
had to: clean synthetic input is what let the original bug hide.

**Next step:** measure whether these chunks actually surface in the top-k for the
50 golden questions before spending a re-ingest on them. If they never rank, this
is a cost bug, not a quality bug, and the priority changes accordingly.
