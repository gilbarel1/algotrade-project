---
name: repo-structure
description: Audit and fix the repository layout after adding files or extending logic — keep the tree consistent with docs/design.md §10, home loose modules into the right package, and sync README/design trees with reality. Use when new modules are created, when a build step adds files, or when asked to review/refactor project structure.
---

# Repo Structure Audit & Optimization

Keep the repository organized and consistent with `docs/design.md` §10 (the source of
truth). Run this after adding files, extending logic, or completing a build step.

## Ground rules (from CLAUDE.md — non-negotiable)

1. **The design doc wins.** Any file move/rename requires updating the §10 tree in
   `docs/design.md` in the same change, and flagging the deviation in the step report.
2. **Never restructure mid-step without being asked.** Structure changes are their own
   change (own commit), not smuggled into a feature step.
3. Moves use `git mv` so history is preserved.

## Placement taxonomy (where new code goes)

Inside `quant_service/`, every module belongs to exactly one package by concern:

| Concern | Package | Examples |
|---|---|---|
| HTTP endpoint (thin — no business logic) | `routers/` | one module per §5 endpoint |
| Data access, external sources, DuckDB persistence | `data/` | `yahoo.py`, `newsapi.py`, `maya.py`, `rss.py`, `cache.py`, `ingest.py` |
| Indicator computation (pandas-ta) | `indicators/` | `calc.py` |
| Sentiment models & language detection | `nlp/` | `finbert.py`, `hebert.py`, `language_detect.py` |
| PDF rendering & charts | `pdf/` | `render.py`, `charts.py` |
| Pydantic schemas (every LLM boundary) | `schemas/` | one module per agent |
| Observability (cost logging/reporting) | `ops/` | `cost_log.py`, `cost_report.py` |

Outside `quant_service/`: n8n workflows → `n8n/`, prompts → `prompts/`, eval harness →
`eval/`, config → `config/`, docs → `docs/`. Generated artifacts (`reports/`,
`store.duckdb`, HF cache) stay gitignored — never committed.

**Conventions:**
- **CLIs live next to the package they drive**, run as `python -m <pkg>.<mod>`
  (e.g. `python -m data.ingest`, `python -m ops.cost_report`, `python -m eval.run`).
  There is NO central `scripts/` folder — do not create one.
- **Routers stay thin**: parse request → call library package → shape §5 response.
  If logic grows inside a router, extract it into the owning package.
- **One-file packages are allowed only when they mirror an existing pattern**
  (e.g. `indicators/` mirrors `nlp/` backing an endpoint). Don't create a package
  for a single helper with no endpoint or concern behind it.
- Heavy data (OHLC arrays, news bodies, HTML) moves through DuckDB/the quant
  service — never through prompts or n8n payloads.

## Audit checklist (run each direction)

1. **Loose files:** any `.py` at `quant_service/` top level besides `app.py`?
   → Home it per the taxonomy above (known accepted exceptions: `store_init.py`,
   `smoke_test.py` — flag them, but moving them requires a §10 update first).
2. **Doc → reality:** every file in the §10 tree exists at that path.
3. **Reality → doc:** every tracked source file appears in the §10 tree (and the
   README tree, which must stay in sync with §10).
4. **Stale references:** after any move, grep the whole repo (code, docstrings,
   README, docs/) for the old module name and old run commands. Zero hits allowed.
5. **Import style:** intra-`quant_service` imports are absolute from the service
   root (`from data import cache`) — the app runs with cwd `quant_service/`.
   Keep new modules consistent with their siblings.
6. **Naming:** module names state their scope honestly (an OHLC-only ingester in a
   growing package may deserve `ingest_ohlc.py` — raise it, don't silently rename).

## Verification (required before claiming done)

From `quant_service/` with the venv active:

```bash
python -c "import app"                      # full FastAPI app loads
python -m <moved.module> --help             # any moved CLI still runs
```

Plus a grep proving no stale references remain. Report what moved, what doc lines
changed, and what was verified — exactly like a CLAUDE.md step report.

## Known deferred improvements (raise, don't do unilaterally)

- `quant_service` is not an installable package; it claims generic top-level names
  (`data`, `ops`, `nlp`) on `sys.path` via cwd. The robust fix (package +
  `pyproject.toml`, `quant_service.*` imports) needs a design-doc change first.
- `store_init.py` / `smoke_test.py` are absent from §10.
