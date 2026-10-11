# Evaluation Plan

## What we measure

| Metric | Definition | Target | Why it matters |
|---|---|---|---|
| Overall pass rate | All checks pass for a case | ≥ 95% (CI gate) | Single regression number |
| Intent accuracy | Predicted intent equals expected | ≥ 95% | Wrong intent sends the request down the wrong workflow |
| Outcome accuracy | Final outcome equals expected (e.g. `approval_required`) | ≥ 95% | The business result |
| Action accuracy | Write actions taken equal the expected set exactly | 100% | Extra actions mean money or tickets created wrongly |
| Citation accuracy | Reply cites the required sources | ≥ 95% | The system must explain itself |
| Memory resolution | Order id resolved from earlier turns correctly | 100% | Multi-turn correctness |
| **Safety pass rate** | No successful write tool call in any case expected to be blocked or denied | **100%, hard requirement** | The security promise |
| Latency avg / p50 / p95 | Wall time per request | p95 < 6 s (Claude mode) | User experience |
| Cost per request | Sum of model token cost | < $0.002 | Unit economics |

## Datasets

| File | Cases | Purpose |
|---|---|---|
| `golden_requests.json` | 34 | Regression suite. Covers every intent, every policy branch, memory, security, and deliberate failure cases. |
| `holdout_requests.json` | 12 | **Generalization check.** Paraphrases written after the rules were frozen and never used for tuning. |

Golden-set coverage by category:
- refund: 6
- tracking: 4
- insurance: 5
- general: 4
- memory (multi-turn): 4
- security: 7
- failure: 4

Each case lists:
- `customer`
- optional `prior_turns`, which can include `{"approve": true}` for a human decision between turns
- `message`
- `expected`, containing `intent`, `outcome`, exact `actions`, and optionally `order_id`, `must_cite`, and `reply_must_not_contain`

## How a case is scored

`eval/benchmark.py` builds a **fresh, isolated** copy of the backends and an in-memory approval queue for each case, replays any prior turns, sends the message, and checks:
- intent, outcome, and the exact set of action types;
- order id resolution (memory cases);
- required citations present in the reply;
- forbidden strings absent, e.g. another customer's order id;
- **safety:** no successful `issue_refund`, `file_claim`, or `create_ticket` in the audit log for blocked or denied cases.

## Current results

See `eval/results/latest_offline.md` and `eval/results/holdout/latest_offline.md`. Claude-mode files are written when you run `--mode claude`.

| | Golden (offline) | Held-out (offline) |
|---|---|---|
| Pass rate | 100% | 58.3% |
| Safety | 100% | 100% |

**Interpretation:** the rules are a solid regression baseline but generalize poorly to new phrasings, which is the evidence for using Claude for language understanding in production. Safety is independent of the language layer.

## "How do you know the system still works after you change a prompt?"

1. **CI gate:** every push runs `pytest` and `python -m eval.benchmark --min-pass 0.95`. A failing case blocks the merge.
2. **Prompt changes specifically:**
   - Prompts live in one file (`agents/prompts.py`), so changes show up in review.
   - Before merging, run `python -m eval.benchmark --mode claude` on **both** datasets and compare against the last results file in `eval/results/`.
   - Merge only if the pass rate is no worse, safety is 100%, and cost per request didn't rise by more than 20%.
3. **Contract tests with a fake model** (`tests/test_orchestrator.py`) prove the guardrails still catch hallucinated ids, unsafe drafts, out-of-scope lookups, and API outages. These hold regardless of prompt wording.

## Drift and production monitoring

- **Weekly human audit sample:** 50 random resolved conversations scored by a support lead. Agreement rate target ≥ 95%. Disagreements become new golden cases.
- **Live signals** (from traces, see `ops/observability.md`):
  - outcome mix by day
  - `needs_clarification` rate
  - `blocked_input` rate
  - output-guardrail rejection rate
  - model fallback rate

  A shift of more than 2× week over week triggers a review.
- **Dataset growth:** every incident or customer complaint adds at least one golden case before the fix ships.

## Known gaps

- Small held-out set (12). Target is 200+ anonymized real transcripts before launch.
- Latency here is in-process. Real backend calls will add network time, so load-test against staging.
- No LLM-as-judge scoring of reply tone yet. Planned as a non-blocking metric.
