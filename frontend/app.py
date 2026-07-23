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
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_STATIC_DIR = Path(__file__).parent / "static"

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

    return {"reply": reply, "degraded": False}


@router.get("/")
def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.include_router(router)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
