"""FinBERT (ProsusAI/finbert) English sentiment (§4.1, §5 /sentiment).

Lazy singleton pipeline: the first call downloads/loads the weights (cached under
HF_HOME), later calls reuse the in-process pipeline. Scores map the full softmax
to a single -1..+1 polarity: P(positive) - P(negative).
"""

import os
import ssl
import threading
from typing import List

MODEL_ID = "ProsusAI/finbert"

_pipeline = None
_lock = threading.Lock()
_tls_configured = False


def _configure_hf_tls() -> None:
    """Make huggingface_hub's httpx client trust the OS/corporate root CAs.

    Same Windows-behind-a-TLS-proxy problem `data.yahoo.configure_tls` solves for
    yfinance, but huggingface_hub 1.x downloads over `httpx`, which pins certifi
    and ignores the `SSL_CERT_FILE` env var that fix relies on. We reuse that
    helper to build the certifi + Windows-store CA bundle, then register an
    httpx client factory that verifies against it. `VERIFY_X509_STRICT` is cleared
    because a legitimate corporate MITM CA can be structurally non-compliant
    (basicConstraints not marked critical) — OpenSSL 3.x rejects it in strict mode
    even though the OS trusts it. Chain, hostname, and expiry checks stay ON; this
    is not `verify=False`. No-op if the bundle can't be built (falls back to
    certifi defaults rather than crash).
    """
    global _tls_configured
    if _tls_configured:
        return
    _tls_configured = True

    try:
        import httpx
        import huggingface_hub
        from huggingface_hub.utils._http import hf_request_event_hook

        from data.yahoo import configure_tls

        configure_tls()  # builds certifi + Windows-store bundle, sets SSL_CERT_FILE
        bundle = os.environ.get("SSL_CERT_FILE")
        if not bundle or not os.path.exists(bundle):
            return

        ctx = ssl.create_default_context(cafile=bundle)
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

        def _client_factory() -> "httpx.Client":
            return httpx.Client(
                event_hooks={"request": [hf_request_event_hook]},
                follow_redirects=True,
                timeout=None,
                verify=ctx,
            )

        huggingface_hub.set_client_factory(_client_factory)
    except Exception:  # noqa: BLE001 - TLS tuning must never block model loading
        return


def polarity_from_labels(label_scores: List[dict]) -> float:
    """Collapse a full label distribution to a -1..+1 polarity score.

    Matching is case-insensitive substring on "pos"/"neg" because HeBERT ships
    misspelled labels in some revisions ("possitive", "negetive", "natural");
    substring matching covers both the typo'd and corrected label sets. A label
    matching neither contributes 0 (neutral) rather than crashing.
    """
    pos = neg = 0.0
    for entry in label_scores:
        label = str(entry.get("label", "")).lower()
        score = float(entry.get("score", 0.0))
        if "pos" in label:
            pos = score
        elif "neg" in label:
            neg = score
    return pos - neg


def load_pipeline(model_id: str):
    """Build a text-classification pipeline with weights cached under HF_HOME.

    HF_HOME must be set before transformers is imported (huggingface_hub reads
    it at import time); default it to the .env.example value so model, tokenizer,
    and config all cache to the same repo-local directory.
    """
    os.environ.setdefault("HF_HOME", ".hf_cache")
    _configure_hf_tls()  # trust OS/corporate CAs for the HF download (see above)
    from transformers import pipeline  # heavy import deferred to first use

    return pipeline(
        "text-classification",
        model=model_id,
        top_k=None,  # full label distribution, not just the argmax
    )


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                # Assign only on success so a failed load retries next request.
                _pipeline = load_pipeline(MODEL_ID)
    return _pipeline


def score_batch(texts: List[str]) -> List[float]:
    """Score a batch of English texts; returns one -1..+1 polarity per text."""
    if not texts:
        return []
    pipe = _get_pipeline()
    # truncation: BERT-family models hard-fail past 512 tokens; a long article
    # body must degrade to its head, not 500 the endpoint.
    results = pipe(texts, truncation=True)
    return [polarity_from_labels(r) for r in results]
