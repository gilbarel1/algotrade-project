# Screenshots

Images used by the top-level `README.md` and `docs/results.md`.

## Auto-generated from a real report PDF

These are rasterized (2×) from `reports/2026-07-21/1403/report.pdf` — a real chat-initiated run
for **NFLX** (Netflix, US market). Regenerate them from any newer report with the snippet at the
bottom of this file.

| File | Report page | Shows |
|---|---|---|
| `report_exec_summary.png` | Executive summary | Run header (mode, watchlist) + highest-conviction calls |
| `report_ticker_page.png` | Per-ticker page | Dual-sentiment panel (LLM vs. model + disagreement), earnings figures with `ambiguous` confidence markers, technical snapshot, news citations |
| `report_reasoning_trace.png` | Reasoning trace | The three-pass Risk Manager loop: draft → devil's-advocate critique → final decision |

## Capture-by-hand (placeholders)

These two show the live UI and can't be pulled from a PDF. Capture them once and drop them in
with these exact names; the README references them.

| File | How to capture |
|---|---|
| `n8n_orchestrator_canvas.png` | With `npm run dev` running, open the **Orchestrator** workflow at <http://localhost:5678> and screenshot the canvas (fan-out → agents → Risk Manager). |
| `chat_ui.png` | Open the chat front end at <http://localhost:8001>, ask *"what do you think about Teva?"*, and screenshot the answer (call + conviction + rationale). |

## Regenerating the report screenshots

```bash
# from the repo root, with the quant_service venv:
quant_service/.venv/Scripts/python - <<'PY'
import fitz
doc = fitz.open('reports/2026-07-21/1403/report.pdf')   # <- newest report
mat = fitz.Matrix(2.0, 2.0)
targets = {1:'report_exec_summary.png', 2:'report_ticker_page.png', 3:'report_reasoning_trace.png'}
FOOTER = 'educational, not investment advice'
for idx, name in targets.items():
    page = doc[idx-1]
    r = fitz.Rect()
    for b in page.get_text('blocks'):
        if FOOTER in b[4]:
            continue
        r |= fitz.Rect(b[:4])
    for d in page.get_drawings():
        r |= d['rect']
    pr = page.rect
    clip = fitz.Rect(pr.x0, max(pr.y0, r.y0-16), pr.x1, min(pr.y1, r.y1+16))
    page.get_pixmap(matrix=mat, clip=clip).save(f'docs/screenshots/{name}')
PY
```

(`pip install pymupdf` if `fitz` is missing. Page numbers assume a single-ticker report; a
multi-ticker report has one ticker page + one reasoning-trace page per ticker.)
