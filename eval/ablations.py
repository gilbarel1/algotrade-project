"""Ablation harness — what does each AI technique actually buy? (§7, §9)

`eval/run.py` measures how well each agent performs. It cannot say whether the
*techniques* are doing the work: a self-consistency vote, a few-shot block and a
three-pass critique all cost real tokens, and "we used self-consistency" is a
claim about effort, not about effect. This module measures the effect by
removing each technique and re-scoring.

Three ablations:

1. **Self-consistency (n=3 majority vote  vs  n=1 single sample).**
   Paired design: the three samples are drawn **once** and both arms are scored
   from the same draws, so the comparison costs no more than one extractor run
   and is not confounded by sampling luck. The n=1 arm is scored at each of the
   three sample positions and averaged, which is exactly the expected behaviour
   of an extractor that stopped after its first answer.

2. **Few-shot prompting (examples  vs  none).** The same sentiment fixtures and
   the same model, with `prompts/sentiment_examples.jsonl` included or omitted.

3. **The three-pass critique loop (final  vs  draft).** Free: every persisted
   recommendation already stores `draft`, `critique` and `final`, so the
   critique's effect is mined from `recommendations` rather than re-run. This
   arm needs no OpenRouter key and no service.

Usage (service running, OPENROUTER_API_KEY set):
    python -m eval.ablations                # all three
    python -m eval.ablations --critique-only  # arm 3 only; free, no LLM calls

Costs are logged to `costs` under an `abl-*` run id, like the eval harness (§9.4).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Dict, List, Optional

import yaml
from tqdm import tqdm

from eval.run import (
    EARNINGS_AGENT,
    EARNINGS_DATA,
    EARNINGS_MODEL,
    EXTRACT_FIELDS,
    PROMPTS_DIR,
    REPO_ROOT,
    SENTIMENT_DATA,
    Services,
    _norm,
    aggregate_calls,
    build_extract_prompt,
    build_sentiment_prompt,
    cache,
    label_from_score,
    load_dotenv_into,
    majority_vote,
    read_jsonl,
    utc_now_naive,
    write_costs,
)

CONVICTION_ORDER = {"low": 0, "medium": 1, "high": 2}


# --------------------------------------------------------------------------- #
# 1. self-consistency: majority vote vs. a single sample
# --------------------------------------------------------------------------- #
def single_sample_commit(sample: Optional[dict]) -> Dict[str, dict]:
    """What an n=1 extractor would commit: whatever the one sample said.

    With no second opinion there is nothing to disagree with, so a stated value
    is committed outright. `None` still means "not in the source" — the ablation
    removes the *vote*, not the schema's ability to abstain, so this is the
    fairest possible single-sample baseline rather than a straw man.
    """
    if not sample:
        return {f: {"value": "ambiguous", "confidence": 1} for f in EXTRACT_FIELDS}
    out = {}
    for f in EXTRACT_FIELDS:
        raw = sample.get(f)
        out[f] = (
            {"value": "ambiguous", "confidence": 1}
            if raw is None or str(raw).strip() == ""
            else {"value": str(raw), "confidence": 1}
        )
    return out


def score_commits(commits: Dict[str, dict], disc: dict, acc: Counter) -> None:
    """Score one disclosure's committed figures against its labels (eval/run.py rules)."""
    for field in EXTRACT_FIELDS:
        truth = disc["truth"]["figures"].get(field)
        value = commits[field]["value"]
        committed = value != "ambiguous"
        if truth is not None:
            if committed and _norm(value) == _norm(truth):
                acc["tp"] += 1
            elif committed:
                acc["fp"] += 1
                acc["fn"] += 1
            else:
                acc["fn"] += 1
        else:
            acc["absent_total"] += 1
            if committed:
                acc["fp"] += 1
                acc["invented"] += 1  # a figure its source does not state
            else:
                acc["absent_ambiguous"] += 1


def _rates(acc: Counter) -> dict:
    tp, fp, fn = acc["tp"], acc["fp"], acc["fn"]
    absent = acc["absent_total"]
    return {
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "recall": tp / (tp + fn) if (tp + fn) else None,
        "absent_total": absent,
        "absent_ambiguous": acc["absent_ambiguous"],
        "invented": acc["invented"],
        "invented_rate": acc["invented"] / absent if absent else None,
    }


def ablate_self_consistency(svc: Services, data: List[dict]) -> dict:
    """n=3 majority vote vs. n=1, scored from one shared set of draws."""
    voted = Counter()
    single = [Counter() for _ in range(3)]

    for disc in data:
        prompt = build_extract_prompt(disc)
        samples = [
            svc.llm_json(EARNINGS_AGENT, EARNINGS_MODEL, prompt, 0.3, "earnings_extraction")
            for _ in range(3)
        ]
        score_commits(majority_vote(samples), disc, voted)
        for i, sample in enumerate(samples):
            score_commits(single_sample_commit(sample), disc, single[i])

    per_position = [_rates(c) for c in single]
    combined = Counter()
    for c in single:
        combined.update(c)
    return {
        "n_disclosures": len(data),
        "voted": _rates(voted),
        "single_mean": _rates(combined),  # pooled over the 3 positions
        "single_positions": per_position,
    }


# --------------------------------------------------------------------------- #
# 2. few-shot prompting: examples vs. none
# --------------------------------------------------------------------------- #
def _score_sentiment_arm(
    svc: Services, data: List[dict], few_shot: List[dict], bull: float, bear: float
) -> dict:
    scores: Dict[str, float] = {}
    for start in range(0, len(data), 10):
        batch = data[start : start + 10]
        payload = svc.llm_json(
            "sentiment", "anthropic/claude-haiku-4.5",
            build_sentiment_prompt(batch, few_shot), 0.0, "sentiment",
        )
        if payload is None:
            continue
        by_id = {i["id"]: i["score"] for i in payload.get("items", [])}
        for d in batch:
            if d["id"] in by_id:
                scores[d["id"]] = float(by_id[d["id"]])
    scored = [d for d in data if d["id"] in scores]
    correct = sum(1 for d in scored if label_from_score(scores[d["id"]], bull, bear) == d["label"])
    return {
        "n": len(scored),
        "correct": correct,
        "accuracy": correct / len(scored) if scored else None,
        "mae": (sum(abs(scores[d["id"]] - d["score"]) for d in scored) / len(scored)) if scored else None,
    }


def ablate_few_shot(svc: Services, data: List[dict], shots: List[dict], bull: float, bear: float) -> dict:
    return {
        "with_examples": _score_sentiment_arm(svc, data, shots, bull, bear),
        "without_examples": _score_sentiment_arm(svc, data, [], bull, bear),
        "n_examples": len(shots),
    }


# --------------------------------------------------------------------------- #
# 3. critique loop: final vs. draft, mined from persisted runs (no LLM calls)
# --------------------------------------------------------------------------- #
def ablate_critique_loop() -> dict:
    """Compare each stored `final` against the `draft` the same run produced.

    The draft IS the ablation: it is what a single-pass Risk Manager would have
    emitted, recorded before the critique existed to challenge it.
    """
    con = cache.connect()
    try:
        cols = [r[0] for r in con.execute("DESCRIBE recommendations").fetchall()]
        rows = con.execute("SELECT * FROM recommendations").fetchall()
    finally:
        con.close()

    paired = rec_changed = conv_changed = downgrades = upgrades = 0
    rubber_stamp = with_critique = 0
    transitions: Counter = Counter()

    for row in rows:
        d = dict(zip(cols, row, strict=True))
        try:
            draft = json.loads(d["draft"]) if d["draft"] else None
            final = json.loads(d["final"]) if d["final"] else None
            crit = json.loads(d["critique"]) if d["critique"] else None
        except (json.JSONDecodeError, TypeError):
            continue
        if crit and draft:
            with_critique += 1
            if crit.get("counter_recommendation") == draft.get("recommendation"):
                rubber_stamp += 1
        if not (draft and final):
            continue
        paired += 1
        if draft.get("recommendation") != final.get("recommendation"):
            rec_changed += 1
            transitions[f'{draft.get("recommendation")} -> {final.get("recommendation")}'] += 1
        dc, fc = draft.get("conviction"), final.get("conviction")
        if dc != fc:
            conv_changed += 1
            if CONVICTION_ORDER.get(fc, 9) < CONVICTION_ORDER.get(dc, -1):
                downgrades += 1
            else:
                upgrades += 1

    return {
        "paired": paired,
        "rec_changed": rec_changed,
        "conv_changed": conv_changed,
        "downgrades": downgrades,
        "upgrades": upgrades,
        "transitions": dict(transitions),
        "with_critique": with_critique,
        "rubber_stamp": rubber_stamp,
    }


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.0%}"


def _num(x: Optional[float], nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def build_report(abl_id: str, sc: Optional[dict], fs: Optional[dict], cl: dict, cost: float) -> str:
    L = [f"Ablation summary  ({abl_id})", ""]

    L.append("1. Self-consistency — majority vote (n=3) vs. a single sample (n=1)")
    if sc:
        v, s = sc["voted"], sc["single_mean"]
        L.append(f"   {sc['n_disclosures']} disclosures · both arms scored from the same draws")
        L.append(f"   {'':<22}{'n=3 (voted)':>14}{'n=1 (single)':>14}")
        L.append(f"   {'invented figures':<22}{v['invented']:>14}{s['invented']:>14}")
        L.append(
            f"   {'  of absent fields':<22}"
            f"{str(v['absent_total']):>14}{str(s['absent_total']):>14}"
        )
        L.append(
            f"   {'invented rate':<22}"
            f"{_pct(v['invented_rate']):>14}{_pct(s['invented_rate']):>14}"
        )
        L.append(f"   {'precision':<22}{_num(v['precision']):>14}{_num(s['precision']):>14}")
        L.append(f"   {'recall':<22}{_num(v['recall']):>14}{_num(s['recall']):>14}")
        per = ", ".join(_pct(p["invented_rate"]) for p in sc["single_positions"])
        L.append(f"   single-sample invented rate by draw: {per}")
    else:
        L.append("   skipped")

    L.append("")
    L.append("2. Few-shot prompting — labeled examples vs. none")
    if fs:
        a, b = fs["with_examples"], fs["without_examples"]
        L.append(f"   {fs['n_examples']} examples from prompts/sentiment_examples.jsonl")
        L.append(f"   {'':<22}{'with':>14}{'without':>14}")
        a_acc = "{} ({}/{})".format(_pct(a["accuracy"]), a["correct"], a["n"])
        b_acc = "{} ({}/{})".format(_pct(b["accuracy"]), b["correct"], b["n"])
        L.append(f"   {'accuracy':<22}{a_acc:>14}{b_acc:>14}")
        L.append(f"   {'MAE':<22}{_num(a['mae']):>14}{_num(b['mae']):>14}")
    else:
        L.append("   skipped")

    L.append("")
    L.append("3. Critique loop — final vs. draft (mined from persisted runs, no LLM calls)")
    if cl["paired"]:
        n = cl["paired"]
        L.append(f"   {n} recommendations carrying both a draft and a final")
        L.append(f"   {'call changed':<30}{cl['rec_changed']}/{n} ({cl['rec_changed']/n:.0%})")
        L.append(f"   {'conviction changed':<30}{cl['conv_changed']}/{n} ({cl['conv_changed']/n:.0%})")
        L.append(f"   {'  downgrades / upgrades':<30}{cl['downgrades']} / {cl['upgrades']}")
        if cl["transitions"]:
            L.append(f"   {'transitions':<30}{cl['transitions']}")
        if cl["with_critique"]:
            L.append(
                f"   {'critique agreed with draft':<30}"
                f"{cl['rubber_stamp']}/{cl['with_critique']} "
                f"(argued against {cl['with_critique'] - cl['rubber_stamp']})"
            )
    else:
        L.append("   no persisted recommendations yet — run the orchestrator first")

    L.append("")
    L.append(f"LLM cost (this ablation run): ${cost:.4f}")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablation harness (§7, §9).")
    parser.add_argument(
        "--critique-only",
        action="store_true",
        help="only the critique-loop arm (mined from DuckDB; no LLM calls, no key needed)",
    )
    args = parser.parse_args()

    load_dotenv_into(os.environ)
    base_url = os.environ.get("QUANT_SERVICE_URL", "http://127.0.0.1:8000")
    base_url = base_url.replace("//localhost:", "//127.0.0.1:")
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key.startswith("your-"):
        key = None

    abl_id = "abl-" + utc_now_naive().strftime("%Y%m%d-%H%M%S")
    critique = ablate_critique_loop()

    if args.critique_only:
        print(build_report(abl_id, None, None, critique, 0.0))
        return 0

    rubric = yaml.safe_load((REPO_ROOT / "config" / "rubric.yaml").read_text(encoding="utf-8"))
    bull = rubric["directional"]["sentiment"]["bullish_threshold"]
    bear = rubric["directional"]["sentiment"]["bearish_threshold"]

    earn = read_jsonl(EARNINGS_DATA)
    sent = read_jsonl(SENTIMENT_DATA)
    shots = read_jsonl(PROMPTS_DIR / "sentiment_examples.jsonl")

    svc = Services(base_url, key)
    sc = fs = None
    total = 0.0
    try:
        if not key:
            print("OPENROUTER_API_KEY not set — LLM arms skipped.", file=sys.stderr)
        elif not svc.service_up():
            print(f"quant service unreachable at {base_url} — LLM arms skipped.", file=sys.stderr)
        else:
            # 3 extraction samples per disclosure + 2 sentiment arms in batches of 10.
            units = len(earn) * 3 + 2 * ((len(sent) + 9) // 10)
            svc.progress = tqdm(total=units, desc="Ablating", unit="call")
            try:
                sc = ablate_self_consistency(svc, earn)
                fs = ablate_few_shot(svc, sent, shots, bull, bear)
            finally:
                svc.progress.close()
                svc.progress = None

        rows = aggregate_calls(svc.calls)
        total = sum(r.get("usd_cost", 0.0) for r in rows)
        print(build_report(abl_id, sc, fs, critique, total))
        if rows:
            con = cache.connect()
            try:
                write_costs(con, abl_id, rows)
            finally:
                con.close()
    finally:
        svc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
