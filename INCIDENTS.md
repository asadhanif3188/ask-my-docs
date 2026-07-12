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
