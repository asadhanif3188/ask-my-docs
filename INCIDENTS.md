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
**Fix:** Not yet applied — this weakens the hallucination gate, the project's
core security guarantee, so it needs an explicit decision rather than a quiet
patch. Recommended: compare with *all* whitespace removed on both sides
("verbatim up to whitespace"). That preserves the actual security property —
the model still cannot introduce content absent from the chunk — while making
validation independent of PDF extraction noise. Should be paired with logging
the `GenerationUnavailable` reason in `query.py` so this class of failure is
never again invisible in the logs.
**Regression guard:** None yet — the fix should land with a test asserting that
`$391,035` validates against a chunk containing `$\n391,035`.
**Time to resolve:** N/A (documented, not fixed).
