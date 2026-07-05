"""POST /sentiment — FinBERT/HeBERT score for a batch of texts (§5, §3.1).

Routes each item by language (explicit field wins, else Hebrew-codepoint
detection): EN -> ProsusAI/finbert, HE -> avichr/heBERT_sentiment_analysis.
Items are scored in one batched pipeline call per model and reassembled into
the original order. Weights are cached under HF_HOME; a model that fails to
load (e.g. offline before the cache is warm) yields a degraded §5-shaped
response, never an unhandled 500 and never fabricated scores.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from nlp import finbert, hebert, language_detect

router = APIRouter()
logger = logging.getLogger(__name__)

_SCORERS = {"en": ("finbert", finbert), "he": ("hebert", hebert)}


class SentimentItem(BaseModel):
    id: str
    text: str
    language: Optional[str] = None


class SentimentRequest(BaseModel):
    items: List[SentimentItem]


@router.post("/sentiment")
def sentiment(req: SentimentRequest):
    if not req.items:
        return {"scores": [], "summary": "0 items scored."}

    # Group item indices by routed language so each model scores one batch.
    groups = {"en": [], "he": []}
    for i, item in enumerate(req.items):
        groups[language_detect.route(item.language, item.text)].append(i)

    scores = [None] * len(req.items)
    counts = {"en": 0, "he": 0}
    degraded_reasons = []

    for lang, indices in groups.items():
        if not indices:
            continue
        model_tag, module = _SCORERS[lang]
        try:
            values = module.score_batch([req.items[i].text for i in indices])
        except Exception as exc:  # model load/inference failure -> degraded
            logger.warning("sentiment scoring failed for %s: %s", model_tag, exc)
            degraded_reasons.append(f"{model_tag} unavailable ({exc})")
            continue
        for i, value in zip(indices, values):
            scores[i] = {
                "id": req.items[i].id,
                "score": round(float(value), 4) + 0.0,  # normalize -0.0 -> 0.0
                "model": model_tag,
            }
        counts[lang] = len(indices)

    scored = [s for s in scores if s is not None]
    summary = (
        f"{len(scored)} items scored: "
        f"{counts['en']} EN (finbert), {counts['he']} HE (hebert)."
    )
    if degraded_reasons:
        summary = f"degraded: {'; '.join(degraded_reasons)} — {summary}"
    return {"scores": scored, "summary": summary}
