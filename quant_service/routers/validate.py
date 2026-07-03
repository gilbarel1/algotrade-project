"""POST /validate — Pydantic validation service for the n8n LLM boundaries (§5, §9.4).

n8n's embedded Python runtime (Pyodide) cannot import this repo's ``schemas/``
package, so the "Pydantic at every LLM boundary" guardrail is enforced here:
each agent sub-workflow POSTs the raw LLM JSON and gets back
``{valid, errors}``. The schemas in ``schemas/`` stay the single source of
truth — nothing is duplicated into workflow JSON.

Registry grows as agents become real: technical (Step 3), sentiment (Step 5),
earnings (Step 6), risk_manager (Step 7).
"""

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from schemas.technical import TechnicalNarration

router = APIRouter()

# agent name -> Pydantic model guarding that agent's LLM boundary.
SCHEMAS = {
    "technical": TechnicalNarration,
}


class ValidateRequest(BaseModel):
    agent: str
    payload: Dict[str, Any]


@router.post("/validate")
def validate(req: ValidateRequest):
    model = SCHEMAS.get(req.agent)
    if model is None:
        return {
            "agent": req.agent,
            "valid": False,
            "errors": [
                f"unknown agent '{req.agent}'; supported: {sorted(SCHEMAS)}"
            ],
        }

    try:
        model.model_validate(req.payload)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        ]
        return {"agent": req.agent, "valid": False, "errors": errors}

    return {"agent": req.agent, "valid": True, "errors": []}
