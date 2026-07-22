# Phase 3.5 — Live Verification Report

To be completed by Sprite after running the verification package in the WSL environment. Fill in every section — leave nothing blank; write "N/A" with a reason if something genuinely doesn't apply.

**How to run everything:**
```bash
python -m verification.run_all_verifications
```
Or run each stage individually (see `verification/` for each script) if you want to inspect one area at a time.

---

## Environment

| Field | Value |
|---|---|
| Python version | |
| pytest version | |
| duckdb version | |
| yfinance version | |
| OS | |
| Git commit | |
| Execution date/time | |

---

## Live Source Verification

### MAS SORA
**Result:** PASS / FAIL
**Notes:** (rows received, coverage dates, any warnings, anything about the `resource_id`/field-name assumption in `config.py` that needed correcting)

### FRED (EFFR)
**Result:** PASS / FAIL
**Notes:** (which path was used — `FRED_API_KEY` vintage path or the no-key CSV fallback — rows received, coverage dates)

### Yahoo Finance (USDSGD=X)
**Result:** PASS / FAIL
**Notes:** (rows received, coverage dates)

---

## Database Verification

**Result:** PASS / FAIL

| Check | Result | Notes |
|---|---|---|
| Row counts (per series) | | |
| Duplicate primary keys | | |
| NULL values | | |
| Invalid dates (future `obs_date`) | | |
| `as_of_date < obs_date` | | |
| Revision integrity | | |
| Value sanity bounds | | |

**Integrity notes:** (anything unexpected, even if not a hard failure)

---

## Idempotency Verification

**Result:** PASS / FAIL

**Run 1 observations:** (rows inserted per series)

**Run 2 observations:** (rows inserted/updated per series — should be ~0 inserted, 0 or explainable updates)

**Notes:**

---

## Rollback Verification

**Result:** PASS / FAIL

**Notes:** (confirm: rollback occurred, zero partial rows, failure was reported clearly, correct exception type raised)

---

## Logging Verification

**Result:** PASS / FAIL

**Notes:** (confirm: failure messages were specific and readable, not generic; process exit code was non-zero on a forced failure; no failure was ever silently reported as success)

---

## Overall Result

**PASS / FAIL**

**Supporting comments:**

---

## Sign-off

Only mark Phase 3 as fully validated if every section above is PASS, or every FAIL has an accompanying explanation of why it's acceptable to proceed anyway. A blank template is not a completed verification.
