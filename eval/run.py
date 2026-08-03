"""`python -m eval.run` — evaluation harness (§9).

Scores the two LLM agents that carry the AI techniques (§7) against the
hand-labeled fixtures in this package and prints a one-page summary. The
harness's own LLM calls are cost-logged to `costs` under an `eval-*` run id
(§9.4); it persists nothing else — the metrics are printed and reproduced in
the README (§9.3).

Five arms map to the §9.2 table:

    Sentiment (LLM)            Haiku 4.5 over /validate boundary  -> label acc, score MAE
    Sentiment (FinBERT/DictaBERT) POST /sentiment                 -> label acc per language
    Sentiment (agreement)      Pearson r between the two score sets
    Earnings (classifier)      Grok temp 0                        -> macro-F1(kind), materiality acc
    Earnings (extractor)       Grok temp 0.3 x3 + majority vote   -> precision, recall

The LLM arms mirror the exact prompts, models, few-shot files, and §3.2
self-consistency vote used by the n8n sub-workflows (`n8n/agents/*.json`), so
the eval measures production behavior rather than a re-implementation. The ML
models are reached over HTTP via the running quant service (architecture
guardrail §2); the majority-vote and label-mapping logic are ported verbatim
from the workflow / rubric so the eval and the pipeline agree.

Run from the repo root with the service up:

    npm run eval                 # loads .env, uses the venv
    python -m eval.run           # requires the venv python + service running
    python -m eval.run --no-llm  # transformer arm only (no OpenRouter key needed)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import yaml
from tqdm import tqdm

# The cost-log and DB helpers live in the quant service; import them rather than
# duplicate the §7 price table or the DUCKDB_PATH resolution (single source of
# truth). They depend only on duckdb/pandas, not on the service's cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = REPO_ROOT / "quant_service"
sys.path.insert(0, str(SERVICE_DIR))
from data import cache  # noqa: E402
from data.tls import ssl_context  # noqa: E402
from ops.cost_log import aggregate_calls, write_costs  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = REPO_ROOT / "prompts"
SENTIMENT_DATA = EVAL_DIR / "sentiment_labeled.jsonl"
EARNINGS_DATA = EVAL_DIR / "earnings_labeled.jsonl"
CHAT_REFUSAL_DATA = EVAL_DIR / "chat_refusal_labeled.jsonl"

# §7 model assignments — same ids the n8n Sentiment / Earnings agents use.
SENTIMENT_MODEL = "anthropic/claude-haiku-4.5"
EARNINGS_MODEL = "x-ai/grok-4.3"
CHAT_MODEL = "anthropic/claude-haiku-4.5"  # §6.5 chat router
# Cost-log agent names (§9.4) — match the n8n sub-workflow agent tags.
SENTIMENT_AGENT = "sentiment"
EARNINGS_AGENT = "earnings"
CHAT_AGENT = "chat"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
KIND_CLASSES = ["earnings", "guidance", "material_event", "other"]
EXTRACT_FIELDS = ["revenue", "eps", "guidance"]


# --------------------------------------------------------------------------- #
# env + IO
# --------------------------------------------------------------------------- #
def load_dotenv_into(env: dict) -> None:
    """Populate missing keys from repo `.env` (KEY=VALUE, quotes/comments handled).

    `npm run eval` already injects .env into the process; this fallback lets a
    bare `python -m eval.run` work too. A value already in the environment wins.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value and value[0] in "\"'":
            end = value.find(value[0], 1)
            value = value[1:] if end == -1 else value[1:end]
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()
        env.setdefault(key, value)


def read_jsonl(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def utc_now_naive() -> datetime:
    # DuckDB TIMESTAMP drops tz; store naive UTC (repo convention).
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# HTTP: quant service + OpenRouter
# --------------------------------------------------------------------------- #
class Services:
    def __init__(self, base_url: str, openrouter_key: Optional[str]):
        self.base_url = base_url.rstrip("/")
        self.openrouter_key = openrouter_key
        # OS-trust TLS anchor (same as the news fetch) so OpenRouter's HTTPS verifies
        # behind a TLS-inspecting proxy — verification stays on (§ security guardrail).
        self.client = httpx.Client(timeout=120.0, verify=ssl_context())
        self.calls: List[dict] = []  # per-LLM-call usage, aggregated into `costs`
        self.progress: Optional[tqdm] = None  # ticks once per LLM boundary (set by main)

    def close(self) -> None:
        self.client.close()

    def service_up(self) -> bool:
        try:
            r = self.client.get(f"{self.base_url}/health", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def score_sentiment(self, items: List[dict]) -> Dict[str, float]:
        """POST /sentiment (FinBERT/DictaBERT). Returns id -> score."""
        payload = {"items": [{"id": it["id"], "text": it["text"], "language": it.get("language")} for it in items]}
        r = self.client.post(f"{self.base_url}/sentiment", json=payload)
        r.raise_for_status()
        return {s["id"]: float(s["score"]) for s in r.json().get("scores", [])}

    def validate(self, agent: str, payload: dict) -> bool:
        r = self.client.post(f"{self.base_url}/validate", json={"agent": agent, "payload": payload})
        r.raise_for_status()
        return bool(r.json().get("valid"))

    def call_llm(
        self,
        cost_agent: str,
        model: str,
        prompt: str,
        temperature: float,
        system: Optional[str] = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        t0 = time.perf_counter()
        r = self.client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {self.openrouter_key}"},
            json={"model": model, "messages": messages, "temperature": temperature},
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        self.calls.append(
            {
                "agent": cost_agent,
                "model": model,
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "latency_ms": latency_ms,
            }
        )
        return data["choices"][0]["message"]["content"]

    def llm_json(
        self, cost_agent: str, model: str, prompt: str, temperature: float, validate_agent: str
    ) -> Optional[dict]:
        """One LLM JSON boundary: parse + /validate, one stricter retry, else None.

        Mirrors the n8n validate -> stricter-retry -> degraded path (guardrail:
        "one automatic retry with a stricter instruction, then a degraded result").
        A transient OpenRouter error (timeout/429/5xx) degrades this one item
        rather than crashing the whole run — degrade-never-500 (§9.4).
        """
        result: Optional[dict] = None
        for _ in range(2):
            try:
                text = self.call_llm(cost_agent, model, prompt, temperature)
            except httpx.HTTPError as exc:
                self._log(f"LLM call failed ({cost_agent}/{validate_agent}): {exc}")
                continue
            payload = parse_json_object(text)
            if payload is not None and self.validate(validate_agent, payload):
                result = payload
                break
            prompt = (
                prompt
                + "\n\nYour previous response was invalid. Respond with ONLY the exact JSON "
                "object specified above — no prose, no markdown fences, all keys present."
            )
        if self.progress is not None:
            self.progress.update(1)  # one tick per boundary, regardless of retries
        return result

    def _log(self, msg: str) -> None:
        """Emit a message without corrupting an active tqdm bar (which owns stderr)."""
        if self.progress is not None:
            self.progress.write(f"  {msg}")
        else:
            print(f"  {msg}", file=sys.stderr)


def parse_json_object(text: str) -> Optional[dict]:
    """Strip markdown fences and parse a single JSON object (as the n8n Parse nodes do)."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- #
# prompt builders — mirror n8n/agents/{sentiment,earnings}.json code nodes
# --------------------------------------------------------------------------- #
def build_sentiment_prompt(items: List[dict], few_shot: List[dict]) -> str:
    shot_lines = "\n".join(
        f'- ({e["language"]}) "{e["text"]}" => score {e["score"]} — {e["reasoning"]}' for e in few_shot
    )
    article_lines = "\n".join(f'- id={a["id"]} ({a.get("language")}) {a["text"]}' for a in items)
    return "\n".join(
        [
            "You are the Sentiment Agent for TA-35 news.",
            "Score each news item's sentiment toward the company on a -1..+1 scale",
            "(-1 very negative, 0 neutral, +1 very positive). Translate Hebrew inline as needed.",
            "",
            "Labeled examples:",
            shot_lines or "(none)",
            "",
            "News items to score:",
            article_lines,
            "",
            "Respond with ONLY a JSON object, no markdown fences, exactly this shape:",
            '{"items":[{"id":"<id>","score":<number -1..1>,"reasoning":"<short, under 300 chars>"}],'
            '"summary":"<overall tone, under 500 chars>"}',
            "Include exactly one entry per news id listed above. Base scores only on the text provided.",
        ]
    )


def build_classify_prompt(disc: dict, few_shot: List[dict]) -> str:
    shots = "\n".join(
        f'- ({e["language"]}) "{e["title"]}" — {e.get("excerpt", "")} => kind={e["kind"]}, '
        f'materiality={e["materiality"]} — {e["reasoning"]}'
        for e in few_shot
    )
    return "\n".join(
        [
            "You are the Earnings Agent for a TA-35 company.",
            "Classify the TASE (Maya) disclosure below and summarize it in English.",
            "kind must be one of: earnings, guidance, material_event, other.",
            "materiality must be one of: low, medium, high.",
            "If the disclosure is in Hebrew, set title_en to a faithful English translation of the title; "
            "if it is already in English, set title_en to null.",
            "Base everything only on the text provided. Do not state any financial figure in the summary "
            "unless it appears verbatim in the text.",
            "",
            "Labeled examples:",
            shots or "(none)",
            "",
            "Disclosure:",
            f'language: {disc["language"]}',
            f'title: {disc["title"]}',
            f'text: {disc.get("excerpt") or "(no further text available)"}',
            "",
            "Respond with ONLY a JSON object, no markdown fences, exactly this shape:",
            '{"kind":"<earnings|guidance|material_event|other>","materiality":"<low|medium|high>",'
            '"summary":"<English, under 500 chars>","title_en":<string or null>}',
        ]
    )


def build_extract_prompt(disc: dict) -> str:
    return "\n".join(
        [
            "You are extracting headline financial figures from a TASE (Maya) disclosure.",
            "Fields: revenue, eps, guidance.",
            "Rules:",
            "- A value must be copied VERBATIM (character-for-character, including currency symbol and "
            "units) from the text below.",
            "- If a figure is not stated explicitly in the text, use null for that field.",
            "- NEVER compute, convert, sum, infer, or guess a number. For Hebrew text, copy the figure "
            "exactly as written; do not translate units.",
            "",
            "Text:",
            f'title: {disc["title"]}',
            f'text: {disc.get("excerpt") or "(no further text available)"}',
            "",
            "Respond with ONLY a JSON object, no markdown fences, exactly this shape:",
            '{"revenue":<string or null>,"eps":<string or null>,"guidance":<string or null>}',
        ]
    )


# --------------------------------------------------------------------------- #
# §3.2 majority vote — ported verbatim from the n8n "Majority Vote" node
# --------------------------------------------------------------------------- #
def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", str(s).lower().strip())
    t = t.replace(",", "")
    t = re.sub(r"\bbillions?\b", "b", t)
    t = re.sub(r"\bmillions?\b", "m", t)
    t = t.replace("מיליארד", "b")  # billion
    t = t.replace("מיליון", "m")  # million
    t = re.sub(r"\bnis\b", "₪", t)
    t = re.sub(r"\bils\b", "₪", t)
    t = t.replace('ש"ח', "₪")
    t = t.replace("שקלים", "₪")
    t = t.replace("שקל", "₪")
    t = re.sub(r"\s*([bm₪$%])\s*", r"\1", t)
    return t


def majority_vote(samples: List[Optional[dict]]) -> Dict[str, dict]:
    """>=2 of 3 samples agree (string-exact after _norm) on a non-null value ->
    commit with confidence = agreeing count; else {'value':'ambiguous','confidence':1}."""
    valid = [s for s in samples if isinstance(s, dict)]
    extracted: Dict[str, dict] = {}
    for field in EXTRACT_FIELDS:
        votes = [(s.get(field), _norm(s.get(field))) for s in valid]
        votes = [(raw, key) for raw, key in votes if key not in (None, "")]
        counts: Dict[str, int] = {}
        raws: Dict[str, str] = {}
        for raw, key in votes:
            counts[key] = counts.get(key, 0) + 1
            raws.setdefault(key, raw)
        committed = None
        for key, n in counts.items():
            if n >= 2 and (committed is None or n > committed[1]):
                committed = (raws[key], n)
        extracted[field] = (
            {"value": str(committed[0]), "confidence": committed[1]}
            if committed
            else {"value": "ambiguous", "confidence": 1}
        )
    return extracted


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def label_from_score(score: float, bull: float, bear: float) -> str:
    if score >= bull:
        return "positive"
    if score <= bear:
        return "negative"
    return "neutral"


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    if sxx == 0 or syy == 0:
        return None
    return sxy / (sxx * syy) ** 0.5


def macro_f1(truth: List[str], pred: List[str], classes: List[str]) -> float:
    f1s = []
    for c in classes:
        tp = sum(1 for t, p in zip(truth, pred, strict=True) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth, pred, strict=True) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth, pred, strict=True) if t == c and p != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


# --------------------------------------------------------------------------- #
# arms
# --------------------------------------------------------------------------- #
def run_sentiment_model(svc: Services, data: List[dict], bull: float, bear: float) -> dict:
    scores = svc.score_sentiment(data)
    scored = [d for d in data if d["id"] in scores]
    correct = sum(1 for d in scored if label_from_score(scores[d["id"]], bull, bear) == d["label"])
    mae = sum(abs(scores[d["id"]] - d["score"]) for d in scored) / len(scored) if scored else None

    # Per language, because this arm is TWO models and one aggregate hides which is
    # carrying the result (§9.2). English goes to FinBERT, Hebrew to DictaBERT, and
    # they do not perform alike — reporting only the mean made the English arm look
    # weak and hid that the Hebrew one was, at one point, not discriminating at all.
    by_language: Dict[str, dict] = {}
    for lang in sorted({d.get("language", "?") for d in scored}):
        sub = [d for d in scored if d.get("language", "?") == lang]
        hits = sum(1 for d in sub if label_from_score(scores[d["id"]], bull, bear) == d["label"])
        by_language[lang] = {
            "n": len(sub),
            "correct": hits,
            "accuracy": hits / len(sub) if sub else None,
            "mae": sum(abs(scores[d["id"]] - d["score"]) for d in sub) / len(sub) if sub else None,
        }

    return {
        "scores": scores,
        "n": len(scored),
        "accuracy": correct / len(scored) if scored else None,
        "correct": correct,
        "mae": mae,
        "by_language": by_language,
    }


def run_sentiment_llm(svc: Services, data: List[dict], few_shot: List[dict], bull: float, bear: float) -> dict:
    scores: Dict[str, float] = {}
    degraded_ids: List[str] = []
    for start in range(0, len(data), 10):  # batches mirror per-ticker article batches
        batch = data[start : start + 10]
        payload = svc.llm_json(
            SENTIMENT_AGENT, SENTIMENT_MODEL, build_sentiment_prompt(batch, few_shot), 0.0, "sentiment"
        )
        if payload is None:
            degraded_ids.extend(d["id"] for d in batch)
            continue
        by_id = {item["id"]: item["score"] for item in payload.get("items", [])}
        for d in batch:
            if d["id"] in by_id:
                scores[d["id"]] = float(by_id[d["id"]])
            else:
                degraded_ids.append(d["id"])
    scored = [d for d in data if d["id"] in scores]
    correct = sum(1 for d in scored if label_from_score(scores[d["id"]], bull, bear) == d["label"])
    mae = sum(abs(scores[d["id"]] - d["score"]) for d in scored) / len(scored) if scored else None
    return {
        "scores": scores,
        "n": len(scored),
        "degraded": degraded_ids,
        "accuracy": correct / len(scored) if scored else None,
        "correct": correct,
        "mae": mae,
    }


def run_earnings_classifier(svc: Services, data: List[dict], few_shot: List[dict]) -> dict:
    truth_kind, pred_kind, truth_mat, pred_mat, degraded = [], [], [], [], []
    for disc in data:
        payload = svc.llm_json(
            EARNINGS_AGENT, EARNINGS_MODEL, build_classify_prompt(disc, few_shot), 0.0, "earnings"
        )
        if payload is None:
            degraded.append(disc["id"])
            continue
        truth_kind.append(disc["truth"]["kind"])
        pred_kind.append(payload["kind"])
        truth_mat.append(disc["truth"]["materiality"])
        pred_mat.append(payload["materiality"])
    mat_acc = (sum(1 for t, p in zip(truth_mat, pred_mat, strict=True) if t == p) / len(truth_mat)) if truth_mat else None
    return {
        "n": len(truth_kind),
        "degraded": degraded,
        "kind_f1": macro_f1(truth_kind, pred_kind, KIND_CLASSES) if truth_kind else None,
        "materiality_acc": mat_acc,
    }


def run_earnings_extractor(svc: Services, data: List[dict]) -> dict:
    tp = fp = fn = 0
    absent_total = absent_ambiguous = 0
    for disc in data:
        prompt = build_extract_prompt(disc)
        samples = [
            svc.llm_json(EARNINGS_AGENT, EARNINGS_MODEL, prompt, 0.3, "earnings_extraction") for _ in range(3)
        ]
        extracted = majority_vote(samples)
        for field in EXTRACT_FIELDS:
            truth = disc["truth"]["figures"].get(field)
            value = extracted[field]["value"]
            committed = value != "ambiguous"
            if truth is not None:  # figure present in source
                if committed and _norm(value) == _norm(truth):
                    tp += 1
                elif committed:
                    fp += 1  # committed the wrong number
                    fn += 1  # and failed to recover the correct one
                else:
                    fn += 1  # abstained on a present figure
            else:  # figure absent -> correct answer is "ambiguous"
                absent_total += 1
                if committed:
                    fp += 1  # fabricated a figure that isn't in the source
                else:
                    absent_ambiguous += 1
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "absent_total": absent_total,
        "absent_ambiguous": absent_ambiguous,
    }


# --------------------------------------------------------------------------- #
# §6.5 chat router — refusal arm
# --------------------------------------------------------------------------- #
CHAT_WORKFLOW = REPO_ROOT / "n8n" / "chat_assistant.workflow.json"
CHAT_SYSTEM_PROMPT = PROMPTS_DIR / "chat_assistant_system.md"

# Phrases that mark a genuine decline. The chat router has no analytical authority
# (§6.5), so "I don't have that / I can only report what the pipeline returns" is
# the correct answer to a figure request — not a failure mode.
_DECLINE_MARKERS = [
    r"\b(don't|do not|doesn't|does not) have\b",
    r"\b(can't|cannot|can not|unable to|not able to)\b",
    r"\b(won't|will not)\b",
    r"\bno (access|data|result|figure|information|authority)\b",
    r"\b(can |i )?only (report|relay|share|provide|give)\b",
    r"\bnot (available|something i|my role|my place)\b",
    r"\bwouldn't be able\b",
    r"\bi'm not (able|allowed|an analyst)\b",
    # Asserting the role IS the refusal: §6.5 makes "I'm a router, not an analyst"
    # the canonical way to turn down a request for a view of its own.
    r"\bnot an analyst\b",
    r"\b(i'm|i am) a router\b",
]
# Markers that the router is doing its actual job: routing to the pipeline.
_ROUTE_MARKERS = [
    r"\banalys(is|e|ing|ze|zing)\b",
    r"\brun(ning)?\b",
    r"\bpipeline\b",
    r"\bminute\b",
    r"\bteam\b",
]


def load_chat_system_prompt() -> str:
    """The canonical §6.5 system prompt: the prompt file minus its maintainer comment.

    n8n cannot read a repo file from an AI Agent node, so the same text is embedded
    in chat_assistant.workflow.json. That duplication is only safe if it is checked,
    so `check_chat_prompt_drift` compares the two and the summary reports a mismatch.
    """
    raw = CHAT_SYSTEM_PROMPT.read_text(encoding="utf-8")
    return re.sub(r"^<!--.*?-->\s*", "", raw, flags=re.S).strip()


def check_chat_prompt_drift() -> Optional[str]:
    """None if the workflow's embedded system prompt matches prompts/, else a reason."""
    try:
        wf = json.loads(CHAT_WORKFLOW.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"could not read {CHAT_WORKFLOW.name}: {exc}"
    embedded = next(
        (
            n.get("parameters", {}).get("options", {}).get("systemMessage")
            for n in wf.get("nodes", [])
            if n.get("type", "").endswith("langchain.agent")
        ),
        None,
    )
    if embedded is None:
        return "no AI Agent node with a systemMessage in the chat workflow"
    if embedded.strip() != load_chat_system_prompt():
        return "workflow systemMessage differs from prompts/chat_assistant_system.md"
    return None


def _normalize_reply(text: str) -> str:
    """Lowercase and fold smart punctuation to ASCII before pattern matching.

    The model writes "can’t" as often as "can't"; without this, an identical
    refusal scores as a pass or a fail depending on which apostrophe it happened
    to emit, which would make the metric measure typography instead of compliance.
    """
    folded = (text or "").translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return folded.lower()


def run_chat_refusal(svc: Services, data: List[dict]) -> dict:
    """Score the §6.5 router-not-analyst constraint with NO tools attached.

    Withholding the tool is the whole point: it puts the model in exactly the
    situation the guardrail is about — asked for a figure it has no result for. A
    compliant router declines; a non-compliant one invents. Fabrication is scored
    across every item (including the routing controls), because emitting a view of
    its own is a violation whether or not the question was a trap.
    """
    system = load_chat_system_prompt()
    results = []
    for item in data:
        try:
            reply = svc.call_llm(
                CHAT_AGENT,
                CHAT_MODEL,
                item["question"],
                0.0,
                system=system
                + "\n\n(No tool results are available in this turn — you have not called any tool.)",
            )
        except httpx.HTTPError as exc:
            svc._log(f"chat refusal item {item['id']} failed: {exc}")
            reply = ""
        finally:
            if svc.progress is not None:
                svc.progress.update(1)

        low = _normalize_reply(reply)
        fabricated = any(
            re.search(p, low, flags=re.IGNORECASE) for p in item.get("forbidden_patterns", [])
        )
        declined = any(re.search(p, low) for p in _DECLINE_MARKERS)
        routed = any(re.search(p, low) for p in _ROUTE_MARKERS)
        expect = item.get("expect", "refuse")
        passed = (not fabricated) and (declined if expect == "refuse" else routed)
        results.append(
            {
                "id": item["id"],
                "expect": expect,
                "fabricated": fabricated,
                "passed": passed,
                "empty": not reply,
            }
        )

    refuse_items = [r for r in results if r["expect"] == "refuse"]
    route_items = [r for r in results if r["expect"] == "route"]
    return {
        "n": len(results),
        "refusal_correct": sum(1 for r in refuse_items if r["passed"]),
        "refusal_n": len(refuse_items),
        "route_correct": sum(1 for r in route_items if r["passed"]),
        "route_n": len(route_items),
        "fabricated": sum(1 for r in results if r["fabricated"]),
        "failures": [r["id"] for r in results if not r["passed"]],
        "drift": check_chat_prompt_drift(),
    }


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def _fmt(x: Optional[float], nd: int = 2) -> str:
    return " n/a" if x is None else f"{x:.{nd}f}"


def build_summary(eval_id: str, sent: List[dict], earn: List[dict], results: dict, cost_rows: List[dict]) -> str:
    n_en = sum(1 for d in sent if d.get("language") == "en")
    n_he = len(sent) - n_en
    L = []
    L.append(f"Evaluation summary  ({eval_id})")
    L.append(
        f"Sentiment: {len(sent)} items ({n_en} EN, {n_he} HE)   Earnings: {len(earn)} disclosures"
        f"   Chat: {(results.get('chat_refusal') or {}).get('n', 0)} router cases"
    )
    L.append("")
    L.append(f"{'Agent':<30}{'Dataset':<20}Metrics")
    L.append("-" * 78)

    sm = results.get("sentiment_model")
    sl = results.get("sentiment_llm")
    ag = results.get("agreement")
    ec = results.get("earnings_classifier")
    ex = results.get("earnings_extractor")
    skip = results.get("skip_llm", "unavailable")

    def row(agent: str, dataset: str, metrics: str) -> str:
        # 30 wide: "Sentiment (FinBERT/DictaBERT)" is 29 chars and ran into the next column.
        return f"{agent:<30}{dataset:<20}{metrics}"

    if sl is None:
        L.append(row("Sentiment (LLM)", "sentiment_labeled", f"skipped - {skip}"))
    else:
        deg = f" | degraded {len(sl['degraded'])}" if sl["degraded"] else ""
        L.append(
            row(
                "Sentiment (LLM)",
                "sentiment_labeled",
                f"accuracy {_fmt(sl['accuracy'])} ({sl['correct']}/{sl['n']}) | MAE {_fmt(sl['mae'])}{deg}  [haiku]",
            )
        )
    if sm is None:
        L.append(
            row("Sentiment (FinBERT/DictaBERT)", "sentiment_labeled", "skipped - service unavailable")
        )
    else:
        L.append(
            row(
                "Sentiment (FinBERT/DictaBERT)",
                "sentiment_labeled",
                f"accuracy {_fmt(sm['accuracy'])} ({sm['correct']}/{sm['n']}) | MAE {_fmt(sm['mae'])}",
            )
        )
        # The two languages run different checkpoints; the mean of the two says
        # little about either (§9.2).
        _MODEL_OF = {"en": "finbert", "he": "dictabert"}
        for lang, st in sorted((sm.get("by_language") or {}).items()):
            L.append(
                row(
                    f"  └ {lang}",
                    _MODEL_OF.get(lang, lang),
                    f"accuracy {_fmt(st['accuracy'])} ({st['correct']}/{st['n']}) "
                    f"| MAE {_fmt(st['mae'])}",
                )
            )
    if ag is None:
        L.append(row("Sentiment (agreement)", "sentiment_labeled", "n/a - needs both score sets"))
    else:
        L.append(row("Sentiment (agreement)", "sentiment_labeled", f"Pearson r {_fmt(ag['r'])} (n={ag['n']})"))
    if ec is None:
        L.append(row("Earnings (classifier)", "earnings_labeled", f"skipped - {skip}"))
    else:
        deg = f" | degraded {len(ec['degraded'])}" if ec["degraded"] else ""
        L.append(
            row(
                "Earnings (classifier)",
                "earnings_labeled",
                f"macro-F1(kind) {_fmt(ec['kind_f1'])} | materiality acc {_fmt(ec['materiality_acc'])} "
                f"(n={ec['n']}){deg}  [grok]",
            )
        )
    if ex is None:
        L.append(row("Earnings (extractor)", "earnings_labeled", f"skipped - {skip}"))
    else:
        L.append(
            row(
                "Earnings (extractor)",
                "earnings_labeled",
                f"precision {_fmt(ex['precision'])} | recall {_fmt(ex['recall'])} | "
                f"ambiguous-when-absent {ex['absent_ambiguous']}/{ex['absent_total']}  [grok]",
            )
        )

    ch = results.get("chat_refusal")
    if ch is None:
        L.append(
            row("Chat router (§6.5)", "chat_refusal", f"skipped - {results.get('skip_chat') or skip}")
        )
    else:
        fab = (
            "no fabrication"
            if ch["fabricated"] == 0
            else f"FABRICATED {ch['fabricated']}/{ch['n']}"
        )
        fails = f" | failed: {', '.join(ch['failures'])}" if ch["failures"] else ""
        L.append(
            row(
                "Chat router (§6.5)",
                "chat_refusal",
                f"refusal {ch['refusal_correct']}/{ch['refusal_n']} | routing "
                f"{ch['route_correct']}/{ch['route_n']} | {fab}{fails}  [haiku]",
            )
        )
        if ch["drift"]:
            L.append(row("", "", f"WARNING prompt drift - {ch['drift']}"))

    L.append("-" * 78)
    total = sum(r["usd_cost"] for r in cost_rows)
    if cost_rows:
        detail = ", ".join(f"{r['agent']} {r['input_tokens'] + r['output_tokens']:,} tok" for r in cost_rows)
        L.append(f"LLM cost (this run): ${total:.4f}   ({detail})")
    else:
        L.append("LLM cost (this run): $0.0000   (no LLM calls)")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    import os

    parser = argparse.ArgumentParser(description="Evaluation harness (§9).")
    parser.add_argument("--no-llm", action="store_true", help="run only the FinBERT/DictaBERT arm (no OpenRouter)")
    args = parser.parse_args()

    load_dotenv_into(os.environ)
    base_url = os.environ.get("QUANT_SERVICE_URL", "http://127.0.0.1:8000")
    # uvicorn binds the IPv4 loopback; httpx would resolve `localhost` to ::1 first
    # and fail with ECONNREFUSED (the documented ::1 footgun). Pin to 127.0.0.1.
    base_url = base_url.replace("//localhost:", "//127.0.0.1:")
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key.startswith("your-"):
        key = None

    rubric = yaml.safe_load((REPO_ROOT / "config" / "rubric.yaml").read_text(encoding="utf-8"))
    bull = rubric["directional"]["sentiment"]["bullish_threshold"]
    bear = rubric["directional"]["sentiment"]["bearish_threshold"]

    sent = read_jsonl(SENTIMENT_DATA)
    earn = read_jsonl(EARNINGS_DATA)
    chat = read_jsonl(CHAT_REFUSAL_DATA)
    sent_shots = read_jsonl(PROMPTS_DIR / "sentiment_examples.jsonl")
    earn_shots = read_jsonl(PROMPTS_DIR / "earnings_examples.jsonl")

    eval_id = "eval-" + utc_now_naive().strftime("%Y%m%d-%H%M%S")
    svc = Services(base_url, key)
    results: dict = {}

    try:
        service_up = svc.service_up()
        if not service_up:
            print(
                f"WARNING: quant service not reachable at {base_url}; "
                "FinBERT/DictaBERT arm and LLM validation will be skipped.",
                file=sys.stderr,
            )

        run_llm = bool(key) and not args.no_llm and service_up
        if not service_up:
            results["skip_llm"] = "service unavailable"
        elif not key:
            results["skip_llm"] = "OPENROUTER_API_KEY not set"
        elif args.no_llm:
            results["skip_llm"] = "--no-llm"
        else:
            results["skip_llm"] = ""

        # One progress unit per work item: the /sentiment model call, plus every
        # LLM boundary (sentiment batches + classifier + 3 extractor samples each).
        # The bar writes to stderr, so the stdout summary below stays clean.
        # The chat refusal arm scores prose, not a validated JSON boundary, so it
        # needs OpenRouter but not the quant service — gate it independently.
        run_chat = bool(key) and not args.no_llm
        n_sent_batches = (len(sent) + 9) // 10
        total_units = (
            (1 if service_up else 0)
            + (n_sent_batches + 4 * len(earn) if run_llm else 0)
            + (len(chat) if run_chat else 0)
        )
        with tqdm(total=total_units, desc="Evaluating", unit="call", disable=total_units == 0) as bar:
            svc.progress = bar

            # Sentiment model arm (needs the service only).
            if service_up:
                results["sentiment_model"] = run_sentiment_model(svc, sent, bull, bear)
                bar.update(1)
            else:
                results["sentiment_model"] = None

            # LLM arms (Haiku / Grok) — require both the key and the service (/validate).
            if run_llm:
                results["sentiment_llm"] = run_sentiment_llm(svc, sent, sent_shots, bull, bear)
                results["earnings_classifier"] = run_earnings_classifier(svc, earn, earn_shots)
                results["earnings_extractor"] = run_earnings_extractor(svc, earn)
            else:
                results["sentiment_llm"] = None
                results["earnings_classifier"] = None
                results["earnings_extractor"] = None

            # §6.5 router-not-analyst check (no service dependency).
            results["skip_chat"] = (
                "" if run_chat else ("--no-llm" if args.no_llm else "OPENROUTER_API_KEY not set")
            )
            results["chat_refusal"] = run_chat_refusal(svc, chat) if run_chat else None

        # Agreement arm (both score sets present).
        sm, sl = results["sentiment_model"], results["sentiment_llm"]
        if sm and sl:
            common = [d["id"] for d in sent if d["id"] in sm["scores"] and d["id"] in sl["scores"]]
            r = pearson([sm["scores"][i] for i in common], [sl["scores"][i] for i in common])
            results["agreement"] = {"r": r, "n": len(common)} if r is not None else None
        else:
            results["agreement"] = None

        cost_rows = aggregate_calls(svc.calls)
        print(build_summary(eval_id, sent, earn, results, cost_rows))

        # Cost-log this eval's own LLM calls to `costs` under the eval run id (§9.4).
        if cost_rows:
            con = cache.connect()
            try:
                write_costs(con, eval_id, cost_rows)
            finally:
                con.close()
    finally:
        svc.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
