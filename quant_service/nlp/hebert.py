"""DictaBERT (dicta-il/dictabert-sentiment) Hebrew sentiment (§4.1, §5 /sentiment).

Same lazy-singleton pattern as finbert.py; the polarity mapping is shared
(`polarity_from_labels` matches "pos"/"neg" case-insensitively, which covers this
model's capitalised `Positive`/`Neutral`/`Negative` labels unchanged).

**Replaces `avichr/heBERT_sentiment_analysis` (§3.1).** Measured against the §9.1
labeled set, HeBERT returned `neutral` for 10 of 10 Hebrew items — neutral
probability 0.833–0.998, including a "record orders, guidance raised" headline at
0.998 — so its 0.30 accuracy was the base rate of neutral items, not
discrimination. Re-normalising polarity over the polar classes and sweeping the
thresholds both failed to recover it (0.53 and no movement respectively).
DictaBERT scores 0.70 on the same items (MAE 0.32 vs 0.39). The module name is
kept: it is the Hebrew scorer, whichever checkpoint that is.
"""

import threading
from typing import List

from nlp.finbert import load_pipeline, polarity_from_labels

MODEL_ID = "dicta-il/dictabert-sentiment"

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
