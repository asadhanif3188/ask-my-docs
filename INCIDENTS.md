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

## Incident (open, not yet fixed): total DB outage isn't covered by the "degraded" fallback contract

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
**Fix:** Not applied — out of scope for today's "prove the pipeline
end-to-end" pass, and the right fix needs a design decision the pipeline
owner should make (e.g.: should a total DB outage return a distinct `503` with
a structured JSON body? Is there anything sensible to serve when *neither*
retrieval path works, or should this just fail fast and loudly?), not just a
try/except slapped on. Recommended follow-up: add a global exception handler
in `app/main.py` returning `{"detail": ...}` JSON for any unhandled exception,
and decide whether a total-DB-down `/query` should be a distinct 503.
**Regression guard:** None yet — candidate test once the fix design is decided.
**Time to resolve:** N/A (documented, not fixed).
