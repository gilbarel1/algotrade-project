"""POST /report — render the run's PDF from the Risk Manager outputs + run id (§5, §8).

The orchestrator posts ``{run_id, recommendations, summary}`` where each
recommendation is the condensed §6.3 Risk Manager output; the service renders
the §8.1 PDF (WeasyPrint over ``templates/report.html.j2``), enriching the rich
blocks (news citations, Maya disclosure + figures, indicator snapshot, price
chart) from DuckDB (see ``pdf/render.py``), and returns ``{run_id, pdf_path}``.
The orchestrator threads ``pdf_path`` into ``/runs/finish`` → ``runs.report_path``.

The request body is intentionally **loose**: it is not an LLM boundary (the
payload was already Pydantic-validated inside the Risk Manager sub-workflow) and
the wire shape is looser than ``RiskManagerOutput`` (``is_earnings_window`` vs
``is_window``, per-panel ``status``), so validating it strictly here would reject
the real payload.

Rendering **degrades, never 500s** (§9.4): on any failure — including the GTK
runtime missing — the response is ``{run_id, pdf_path: null, summary:
"degraded: <reason>"}``, and the orchestrator maps a falsy ``pdf_path`` to run
status ``error``. An empty ``recommendations`` list still renders a valid
header-only PDF.
"""

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from data import cache
from pdf import render

router = APIRouter()


class ReportRequest(BaseModel):
    run_id: str
    recommendations: List[Dict[str, Any]] = []
    summary: str = ""


@router.post("/report")
def report(req: ReportRequest):
    con = cache.connect()
    try:
        context = render.build_context(
            con, req.run_id, req.recommendations, req.summary
        )
        pdf_path = render.render_pdf(context)
        return {"run_id": req.run_id, "pdf_path": pdf_path}
    except Exception as exc:  # noqa: BLE001 - never 500; degrade with reason (§9.4)
        reason = str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__
        return {
            "run_id": req.run_id,
            "pdf_path": None,
            "summary": f"degraded: report render failed ({reason})",
        }
    finally:
        con.close()
