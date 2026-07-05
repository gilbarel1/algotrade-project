"""HeBERT (avichr/heBERT_sentiment_analysis) Hebrew sentiment (§4.1, §5 /sentiment).

Same lazy-singleton pattern as finbert.py; the polarity mapping is shared
(polarity_from_labels handles HeBERT's typo'd label set).
"""

import threading
from typing import List

from nlp.finbert import load_pipeline, polarity_from_labels

MODEL_ID = "avichr/heBERT_sentiment_analysis"

_pipeline = None
_lock = threading.Lock()


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                # Assign only on success so a failed load retries next request.
                _pipeline = load_pipeline(MODEL_ID)
    return _pipeline


def score_batch(texts: List[str]) -> List[float]:
    """Score a batch of Hebrew texts; returns one -1..+1 polarity per text."""
    if not texts:
        return []
    pipe = _get_pipeline()
    results = pipe(texts, truncation=True)
    return [polarity_from_labels(r) for r in results]
