"""POST /sentiment — FinBERT/HeBERT score for a batch of texts (§5).

Step 0: returns the hardcoded §5 stub. Real FinBERT/HeBERT inference lands in Step 4.
"""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SentimentItem(BaseModel):
    id: str
    text: str
    language: Optional[str] = None


class SentimentRequest(BaseModel):
    items: List[SentimentItem]


@router.post("/sentiment")
def sentiment(req: SentimentRequest):
    return {
        "scores": [
            {"id": "a1", "score": 0.62, "model": "finbert"},
            {"id": "a2", "score": -0.18, "model": "hebert"},
        ],
        "summary": "2 items scored: 1 EN (finbert), 1 HE (hebert).",
    }
