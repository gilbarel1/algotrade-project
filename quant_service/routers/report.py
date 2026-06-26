"""POST /report — render PDF from Risk Manager output + run id (§5).

Step 0: returns the hardcoded §5 stub. Real WeasyPrint/Jinja2 rendering lands in Step 9.
"""

from typing import Any, List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ReportRequest(BaseModel):
    run_id: str
    recommendations: List[Any] = []
    summary: str = ""


@router.post("/report")
def report(req: ReportRequest):
    return {
        "run_id": "r_2026-06-22T13:00",
        "pdf_path": "reports/2026-06-22/1300/report.pdf",
    }
