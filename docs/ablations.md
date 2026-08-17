# Ablations — does each AI technique actually earn its keep?

`npm run eval` measures how well each agent performs. It cannot tell you whether the *techniques*
are doing the work. A self-consistency vote costs 3× the tokens of a single call; a three-pass
critique costs 3× a single pass; a few-shot block inflates every prompt. "We used self-consistency"
is a claim about effort, not about effect.

This harness measures the effect the only way that means anything: **remove the technique and
re-score.**

```bash
npm run ablations                     # all three arms
npm run ablations -- --critique-only  # arm 3 only — free, no key, no service
```

Source: [`eval/ablations.py`](../eval/ablations.py). Costs are logged to the `costs` table under an
`abl-*` run id, exactly like the eval harness (§9.4). The run below cost **$0.0754**.

---

## Results — `abl-20260817-111819`

### 1. Self-consistency: majority vote (n=3) vs. a single sample (n=1)

|                        | n=3 (voted) | n=1 (single) |
| ---------------------- | ----------: | -----------: |
| **Invented figures**   |       **0** |        **2** |
| of absent fields       |          22 |           66 |
| **Invented rate**      |      **0%** |       **3%** |
| Precision              |        1.00 |         0.92 |
| Recall                 |        1.00 |         0.92 |

Single-sample invented rate **by draw: 5%, 0%, 5%.**

That last line is the whole mechanism in one row. Two of the three individual draws each committed a
figure its source does not state — and the majority vote caught both, **because the samples
disagreed with each other.** Self-consistency is not averaging noise away; it is using disagreement
between samples as the signal that a figure is not actually in the document. A single-sample
extractor has no such signal available, and no way to know it got one wrong.

**Design note.** Both arms are scored from the *same three draws*, so the comparison is paired and
cannot be confounded by sampling luck — and it costs no more than one extractor run. The n=1 arm is
scored at each of the three positions and pooled (hence 66 = 3 × 22 absent fields), which is exactly
the expected behaviour of an extractor that stopped after its first answer.

### 2. Few-shot prompting: 9 labeled examples vs. none

|              |        with |     without |
| ------------ | ----------: | ----------: |
| **Accuracy** | **90%** (27/30) | **77%** (23/30) |
| **MAE**      |    **0.12** |        0.20 |

Thirteen accuracy points and a 40% lower error, from nine examples in a version-controlled JSONL
file. The sharper reading is what the "without" column equals: **77% is the same accuracy the free
local transformer arm scores** (see the [evaluation results](../README.md#evaluation-results)).
Strip the examples out and the paid LLM stops being worth paying for — it lands exactly where
FinBERT/DictaBERT already sit, at a per-call cost. The few-shot block is not prompt decoration; it
is the entire margin the LLM arm contributes over the model it is meant to disagree with.

### 3. The three-pass critique loop: final vs. draft

Free arm — every persisted recommendation already stores all three passes, so the counterfactual is
recorded rather than re-run. **The `draft` *is* the ablation**: it is what a single-pass Risk Manager
would have emitted, written down before the critique existed to challenge it.

Over **26** recommendations carrying both a draft and a final:

| Effect                        | Measured                          |
| ----------------------------- | --------------------------------- |
| Call changed                  | **8/26 (31%)** — every one `long → hold` |
| Conviction changed            | **9/26 (35%)**                    |
| Direction of those changes    | **9 downgrades, 0 upgrades**      |
| Critique agreed with the draft | 7/26 — it argued against **19**  |

The striking part is the zero. Across every run on record, the critique pass has **never once** made
the system more bullish or more confident. It only ever pulls back — and when it moves the call, it
moves it out of a directional position into `hold`. That is a measured behavioural property, not a
claim in a prompt: the loop is strictly conservative in practice.

It also isn't a rubber stamp. In 19 of 26 cases the devil's advocate argued for a different
recommendation than the draft, which is what the prompt demands of it
([`prompts/risk_manager_critique.md`](../prompts/risk_manager_critique.md)) and what a
self-agreeing loop would fail to do.

The worked example is in [`results.md`](results.md): the critique caught the draft **miscounting its
own agreement rule** — scoring neutral earnings as a bullish vote — and the final conceded the
arithmetic in writing and downgraded to `hold`/low.

---

## What these numbers do not show

Stated plainly, because an ablation that overclaims is worse than none.

- **Small samples.** Self-consistency rests on 10 disclosures / 22 absent fields per arm; few-shot on
  30 sentiment items; the critique loop on 26 recommendations drawn from a handful of runs over a few
  tickers. These are directional results, not tight confidence intervals.
- **The critique arm is observational, not a controlled experiment.** The draft was produced by a
  prompt that *knows* a critique is coming, so it is not a clean single-pass baseline — a genuinely
  single-pass Risk Manager would likely be written differently. And the 26 rows are not independent:
  they come from repeated runs over an overlapping ticker set.
- **"Changed the call" is not "improved the call."** Nothing here measures whether the `hold` was
  *right*. The system has no realized-return measurement at all (see
  [Limitations](../README.md#limitations)); a backtest is the missing piece, and it is the one that
  would turn "more conservative" into "more accurate."
- **The n=1 arm samples at temperature 0.3**, because it is derived from the same draws as the vote.
  That answers "what if we had stopped at the first sample?" — the right question for this ablation —
  but it is not the same as "what would a temperature-0 single-shot extractor do?"
- **One run, one model each.** Sampling variance is real: the extractor's `ambiguous`-when-absent
  score has measured 22/22, 21/22, 22/22 and 22/22 across four eval runs, so a single ablation run
  should be read with that spread in mind.
