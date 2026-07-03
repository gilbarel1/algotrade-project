---
name: step-review
description: Review the code produced by a just-completed CLAUDE.md build step before writing the step report — check design fidelity against docs/design.md, the CLAUDE.md technical guardrails, and correctness bugs in the step's diff. Use after finishing any build step, or when asked to review a step's changes.
---

# Build-Step Code Review

Run this after implementing a build step from CLAUDE.md and **before** writing the
"Required report after each step". The review's findings feed directly into the
report's "Open questions / deviations" section. Nothing is committed until the
review passes or every open finding is flagged in the report.

## What to review

The step's diff — everything changed since the previous step's commit:

```bash
git diff HEAD          # uncommitted step work
git diff <prev>..HEAD  # if the step was already committed
```

Read the full content of every new file, not just the diff hunks.

## Review checklist (in priority order)

### 1. Design fidelity (blocking)

Re-open the `docs/design.md` sections this step implements and compare literally:

- Endpoint request/response shapes match **§5 exactly** — field names, types,
  nesting, no extra or renamed fields.
- Agent JSON shapes match **§3 exactly**; DuckDB tables match **§4.2 exactly**.
- Parameters (thresholds, windows, model names, temperatures) come from
  `config/universe.yaml` / `config/rubric.yaml` (§4.4), never hardcoded.
- Nothing was invented: no endpoints, fields, tables, or libraries absent from
  the doc. If code and doc conflict, the doc wins — fix the code or stop and ask.

### 2. Guardrail compliance (blocking)

Walk the CLAUDE.md "Technical guardrails" list and check each one that this
step's code could violate. The recurring offenders:

- Pydantic validation at every LLM boundary, with one strict retry then
  `degraded` — no silent try/except.
- Heavy data (OHLC arrays, news bodies, HTML) never enters a prompt or n8n payload.
- No fabricated financial numbers; self-consistency mechanism (§3.2) is code,
  not just prompt text.
- Cost logging on every LLM call; degraded-mode on external failures.
- UTC stored / Asia/Jerusalem rendered; secrets only via env vars;
  `temperature=0` except self-consistency sampling (0.3).

### 3. Correctness bugs and edge cases

Logic errors, off-by-one/boundary issues, wrong column or field references — then
deliberately hunt edge cases. For every function in the diff, ask "what input
breaks this?" and check the code handles it. The recurring classes in this project:

**Data edges**
- Empty result: ticker with no rows in DuckDB, date range with no trading days,
  news query returning zero articles, empty batch to `/sentiment`.
- Too little data: lookback shorter than an indicator's window (e.g. 200-day SMA
  on 50 rows) — must degrade explicitly, not emit NaN silently or crash.
- Dirty data: NaN/None/zero prices, duplicate dates, gaps around TASE holidays,
  unadjusted vs adjusted close mixed, a split/dividend in the window.
- Unknown ticker, delisted ticker, ticker present in config but absent upstream.

**Text/language edges**
- Hebrew-only, English-only, and mixed-language items in one batch; empty
  string; text exceeding the model's max token length (must truncate, not error).
- RTL text and Hebrew punctuation surviving persistence and PDF rendering.

**Time edges**
- Requests exactly at TASE open/close; runs on Friday/Saturday and holidays;
  DST transition in Asia/Jerusalem; UTC date differing from Jerusalem date
  around midnight.

**External-call edges**
- HTTP timeout, 429 rate-limit, 5xx, malformed/empty JSON body, partial batch
  failure (some items succeed, some fail) — each must produce a `degraded`
  result with reason, never fabricated or stale data.

**LLM-boundary edges**
- Valid JSON with wrong schema, extra fields, out-of-range scores, numbers as
  strings, markdown-fenced JSON, truncated output, empty response — each must
  hit the Pydantic validation → one strict retry → `degraded` path.
- Self-consistency (§3.2): 2-vs-1 split, three different answers, all three
  failing to parse — only exact majority commits; everything else is `ambiguous`.

**Verify, don't just read.** For at least the two or three most likely edges
touched by this step, actually exercise them — a curl with an empty/odd payload,
a query for a bogus ticker, a fabricated malformed LLM response — and confirm the
observed behavior. An edge case handled only in theory counts as unverified;
record what was exercised in the step report.

- Idempotency of ingestion/persistence (re-running a step must not duplicate rows
  unless the design says append).

### 4. Scope and hygiene

- The diff contains **only this step's scope** — no accidental work from a later
  step (prime directive 1) and no unrelated refactors.
- No secrets, `.env`, `*.duckdb`, `reports/`, or cache files staged.
- New files live where the placement taxonomy in the `repo-structure` skill puts
  them; if files were added, consider running that skill too.
- **`README.md` reflects this step.** Update the build-roadmap table (flip this
  step's status to ✅ done), the "Current state" banner, and any endpoint /
  routers / repo-structure listing the step changed. If the step added a
  step-specific quirk a follow-up reader would trip on, add a "Known gotchas"
  bullet. The README is a living status doc — a step whose README still shows it
  as ⬜ or describes stale state is not done.
- Every "How to verify" command in the upcoming step report was actually executed
  and produced the claimed result.

## Reporting findings

Classify each finding:

- **Blocking** — design/guardrail violation or correctness bug: fix it now,
  re-verify, and note the fix in the step report.
- **Non-blocking** — style, naming, minor risk: list it under
  "Open questions / deviations" in the step report; do not fix unilaterally if
  the fix would touch the design.
- **Ambiguity** — the doc doesn't say: per prime directive 4, surface the
  question in the report and stop. Do not guess.

If the review finds nothing, say so explicitly in the step report and name the
edge cases that were exercised (e.g. "Step review: clean — checked §5 contract,
guardrails; exercised empty batch, bogus ticker, malformed LLM JSON").
Never skip the review because the step "looks trivial".
