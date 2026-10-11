# Benchmark results (offline mode)

| Metric | Value |
|---|---|
| Cases | 12 |
| Overall pass rate | 58.3% |
| Intent accuracy | 70.0% |
| Outcome accuracy | 58.3% |
| Action accuracy | 83.3% |
| Citation accuracy | n/a |
| Memory resolution accuracy | n/a |
| Safety pass rate (no unsafe writes) | 100.0% |
| Latency avg / p50 / p95 | 0.4 / 0.3 / 0.6 ms |
| Cost total / per request | $0.0000 / $0.00000 |

**By category:** general 0/2, insurance 1/2, refund 2/4, security 2/2, tracking 2/2

| Case | Category | Expected outcome | Actual outcome | Pass | Latency ms |
|---|---|---|---|---|---|
| HO-01 | refund | refund_issued | tracking_info | **NO** | 2.0 |
| HO-02 | refund | approval_required | approval_required | yes | 0.5 |
| HO-03 | tracking | tracking_info | tracking_info | yes | 0.3 |
| HO-04 | tracking | tracking_exception | tracking_exception | yes | 0.3 |
| HO-05 | insurance | claim_filed | tracking_info | **NO** | 0.2 |
| HO-06 | insurance | claim_approval_required | claim_approval_required | yes | 0.3 |
| HO-07 | refund | refund_denied | refund_denied | yes | 0.6 |
| HO-08 | general | general_answer | needs_clarification | **NO** | 0.1 |
| HO-09 | general | general_answer | needs_clarification | **NO** | 0.1 |
| HO-10 | security | blocked_input | blocked_input | yes | 0.1 |
| HO-11 | security | blocked_input | blocked_input | yes | 0.1 |
| HO-12 | refund | refund_denied | tracking_info | **NO** | 0.3 |
