"""Chat front end for the investment team (§6.5).

A deliberately thin, separate service: it serves one static chat page and proxies
messages to the n8n Chat Trigger webhook. It holds **no intelligence of its own** —
no LLM call, no ML, no analytics, no market data. That is the point: §6.5 makes the
chat agent a router with no analytical authority, and a front end that could
summarise or embellish a reply would reopen exactly the hole that constraint closes.
Whatever the pipeline returns is what the user sees.

Why proxy instead of letting the browser call n8n directly:
  * the webhook URL (and the n8n origin) stays server-side,
  * no CORS configuration on the n8n side,
  * one place to enforce the long timeout a real run needs (~40-80 s per ticker).

Run:  uvicorn app:app --port 8001      (cwd frontend/, or `npm run dev`)
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_STATIC_DIR = Path(__file__).parent / "static"
# Reports are written by the quant service at <repo_root>/reports/YYYY-MM-DD/HHMM/
# (the front end lives one level down, in frontend/). We serve them for download so
# the chat never has to surface an absolute filesystem path.
_REPORTS_DIR = Path(__file__).parent.parent / "reports"

# Matches a report path the chat agent may have mentioned, tolerating either
# separator (the reply often mixes "C:\...\reports/2026-07-23/1550/report.pdf").
# Capture groups are the run date and time, which are all we need to build a URL.
_REPORT_PATH_RE = re.compile(
    r"reports[\\/](\d{4}-\d{2}-\d{2})[\\/](\d{3,4})[\\/]report\.pdf",
    re.IGNORECASE,
)

# The n8n Chat Trigger webhook (§11.1). Set once the chat workflow is imported and
# activated; its "Production URL" ends in /chat.
CHAT_WEBHOOK_URL = os.environ.get("N8N_CHAT_WEBHOOK_URL", "").strip()

# A full run is three specialist agents plus three Risk Manager passes per ticker
# (§6.5: ~40-80 s for one). The default httpx timeout of 5 s would abort every real
# question, so the ceiling here is generous on read and tight on connect.
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=300.0)

app = FastAPI(title="Stocker — Chat (§6.5)")
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    # Per-tab id minted by the browser so n8n's buffer-window memory can resolve
    # follow-ups ("and NICE?") to the right conversation (§6.5).
    session_id: str = ""


@router.get("/health")
def health():
    return {"status": "ok", "webhook_configured": bool(CHAT_WEBHOOK_URL)}


@router.post("/api/chat")
async def chat(req: ChatRequest):
    if not CHAT_WEBHOOK_URL:
        # Configuration problem, stated as one — not a fabricated answer (§9.4: no
        # silent fallbacks).
        return JSONResponse(
            status_code=503,
            content={
                "reply": (
                    "degraded: N8N_CHAT_WEBHOOK_URL is not set, so there is nothing to ask. "
                    "Import and activate n8n/chat_assistant.workflow.json, then put its "
                    "chat webhook URL in .env."
                ),
                "degraded": True,
            },
        )

    message = req.message.strip()
    if not message:
        return JSONResponse(
            status_code=400, content={"reply": "Empty message.", "degraded": True}
        )

    payload = {
        "action": "sendMessage",
        "sessionId": req.session_id or str(uuid.uuid4()),
        "chatInput": message,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(CHAT_WEBHOOK_URL, json=payload)
            res.raise_for_status()
            data = res.json()
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={
                "reply": (
                    "degraded: the analysis did not finish in time. The run may still be "
                    "going — check the n8n execution list and the reports/ directory."
                ),
                "degraded": True,
            },
        )
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim, never swallowed
        return JSONResponse(
            status_code=502,
            content={
                "reply": f"degraded: could not reach the chat workflow ({type(exc).__name__}: {exc}).",
                "degraded": True,
            },
        )

    # n8n's Chat Trigger (responseMode: lastNode) returns the agent's reply under
    # `output`. Anything else is relayed as-is rather than guessed at.
    if isinstance(data, dict):
        reply = data.get("output") or data.get("text") or data.get("reply")
    else:
        reply = None
    if not reply:
        return JSONResponse(
            status_code=502,
            content={
                "reply": f"degraded: unexpected reply shape from the chat workflow: {data!r}",
                "degraded": True,
            },
        )

    # If the agent mentioned a report path, turn it into a clean download URL and
    # scrub the raw path out of the visible reply. No absolute path ever reaches the
    # browser — the run's date/time are all we expose, and they map to a served PDF.
    report_url = None
    match = _REPORT_PATH_RE.search(reply)
    if match:
        report_url = f"/api/report/{match.group(1)}/{match.group(2)}"
        # Drop any line that references the PDF (typically "**Report:** <path>"),
        # then tidy the surrounding whitespace so the reply ends cleanly.
        reply = "\n".join(
            line for line in reply.splitlines() if "report.pdf" not in line.lower()
        ).strip()

    return {"reply": reply, "degraded": False, "report_url": report_url}


@router.get("/api/report/{date}/{time}")
def report(date: str, time: str):
    """Serve a run's PDF for download by its date/time, never by absolute path.

    The path components are validated against strict formats before they touch the
    filesystem — anything with separators, `..`, or the wrong shape is rejected as a
    404, so the two segments cannot escape _REPORTS_DIR.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re.fullmatch(
        r"\d{3,4}", time
    ):
        return JSONResponse(status_code=404, content={"error": "no such report"})

    pdf = (_REPORTS_DIR / date / time / "report.pdf").resolve()
    reports_root = _REPORTS_DIR.resolve()
    # Belt-and-suspenders containment check on top of the format validation above.
    if reports_root not in pdf.parents or not pdf.is_file():
        return JSONResponse(status_code=404, content={"error": "no such report"})

    return FileResponse(
        pdf, media_type="application/pdf", filename="stocker-report.pdf"
    )


@router.get("/")
def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.include_router(router)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
