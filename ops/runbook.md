# Runbook

**Service:** SwiftParcel support agents. **Owner:** Platform team (primary on-call), with Support Ops as business owner. **Dashboards and alerts:** see `ops/observability.md`.

## How we detect a failure at 2 AM

| Alert | Fires when | Severity |
|---|---|---|
| `SupportErrorRateHigh` | `system_error` + `action_failed` > 2% over 15 min | Page |
| `SupportNoTraffic` | 0 requests for 15 min during expected traffic | Page |
| `ModelFallbackHigh` | > 5% of model calls failing over 15 min | Warn (service still correct) |
| `OutputGuardrailRejectionsHigh` | > 3% of drafts rejected per day | Warn |
| `BlockedInputSpike` | `blocked_input` > 5% per day, or 3× baseline in 1 hour | Warn |
| `AccessDeniedEnumeration` | > 5 `access_denied` per hour for one customer | Warn, security |
| `ApprovalsStale` | Approval pending > 4 business hours | Warn, support lead |
| `RefundVolumeAnomaly` | Refund count or amount per hour > 3× the 7-day same-hour average | **Page**, possible abuse or bug |
| `CostSpike` | Daily model spend > 2× 7-day average | Warn |

## First 5 minutes (any page)

1. Acknowledge the page and open the dashboard: error rate, latency, outcome mix, fallback rate.
2. Get a failing trace id from the alert or the logs, then pull the full record from `traces.jsonl` and audit entries with the same `trace_id`. The `spans[].status == "error"` and `events[]` fields show where it broke.
3. Check for a recent deploy, and check the Claude status page and backend health.
4. **If money could be moving wrongly, stop it first.** Set `REFUND_APPROVAL_THRESHOLD=0` (every refund and claim then needs a human) and redeploy the config. Investigate after.

## Demo-day emergency switch

If the live demo misbehaves and there's no time to debug, set `MAINTENANCE_MODE=1` in Render → Environment. Visitors see a polished maintenance page within about a minute. Fix, then remove the variable.

## Playbooks

### A. Claude API down or throttled (`ModelFallbackHigh`)
- Impact: none on correctness. The system falls back to rules and templates automatically, with plainer wording and weaker understanding of unusual phrasing.
- On the web demo, a **circuit breaker** opens after 3 failed Claude requests within 2 minutes. Replies are then served instantly in rules-only mode for 5 minutes, then one trial request tests Claude again. `/api/config` shows the breaker state.
- Action: confirm on the provider status page. If it lasts more than 30 minutes, set `LLM_MODE=offline` so requests aren't wasting time on timeouts. Revert when the provider recovers.

### B. Error-rate spike after a deploy
- Roll back to the previous task definition revision (see deployment.md §5). Confirm the error rate recovers.
- Reproduce locally with the failing message: `python main.py ask "<message>" --customer <id> --verbose`.
- Add the case to `eval/golden_requests.json` before fixing.

### C. Suspected prompt-injection attack (`BlockedInputSpike`, `AccessDeniedEnumeration`)
- Confirm no unsafe writes. In the audit log, search for `issue_refund` and `file_claim` with `ok: true` in the window, and check each has a valid policy decision or `approval_id`.
- Rate-limit or block the source at the WAF. Escalate to security.
- Add the new attack phrasing to the held-out set and the guardrail patterns.

### D. Wrong refund or claim paid (`RefundVolumeAnomaly`, or a support report)
1. Stop money movement: set `REFUND_APPROVAL_THRESHOLD=0`.
2. Scope the impact: list refund and claim tool calls from the audit log in the window, joined to traces by `trace_id`, to see the decision and rule ids behind each.
3. Notify finance with the list. Reverse payments through the payments team.
4. Root cause:
   - wrong rule (policy code),
   - wrong data (backend),
   - or wrong intent/order resolution (trace spans).
5. Add regression cases and ship the fix through the normal gate.

### E. Approvals piling up (`ApprovalsStale`)
- Support lead runs `python main.py approvals list` (or opens the console) and works the queue. Customers asking in the meantime get `approval_pending` with their reference.

### F. Model quality regression (guardrail rejections or customer complaints rise)
- Compare `eval/results/latest_claude.md` with the previous release. Revert `CLAUDE_MODEL` or the prompt change.

## Routine operations

| Task | Frequency | How |
|---|---|---|
| Human audit sample (50 conversations) | Weekly | Support lead; disagreements become golden cases |
| Review approvals and auto-refund totals | Weekly | Finance + support ops |
| Rotate API keys | 90 days | Secrets Manager rotation |
| Re-run Claude benchmark | Each release and monthly | `python -m eval.benchmark --mode claude` |
| Red-team injection prompts | Quarterly | Security; new cases go into the held-out set |

## Ownership

| When | Who runs it |
|---|---|
| Launch week | Platform engineers on daily check-ins with support ops; threshold set to $100 for extra caution |
| Steady state | Platform on-call for availability; Support Ops owns policy rules and approvals; Security owns guardrail reviews |
