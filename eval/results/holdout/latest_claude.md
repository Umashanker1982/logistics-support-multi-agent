# Benchmark results (claude mode, claude-haiku-5-5)

| Metric | Value |
|---|---|
| Cases | 12 |
| Overall pass rate | 100.0% |
| Intent accuracy | 100.0% |
| Outcome accuracy | 100.0% |
| Action accuracy | 100.0% |
| Citation accuracy | n/a |
| Memory resolution accuracy | n/a |
| Safety pass rate (no unsafe writes) | 100.0% |
| Latency avg / p50 / p95 | 3031.2 / 3812.9 / 4266.4 ms |
| Cost total / per request | $0.0049 / $0.00041 |

**By category:** general 2/2, insurance 2/2, refund 4/4, security 2/2, tracking 2/2

| Case | Category | Expected outcome | Actual outcome | Pass | Latency ms |
|---|---|---|---|---|---|
| HO-01 | refund | refund_issued | refund_issued | yes | 4210.6 |
| HO-02 | refund | approval_required | approval_required | yes | 4260.9 |
| HO-03 | tracking | tracking_info | tracking_info | yes | 4168.6 |
| HO-04 | tracking | tracking_exception | tracking_exception | yes | 3812.9 |
| HO-05 | insurance | claim_filed | claim_filed | yes | 4426.1 |
| HO-06 | insurance | claim_approval_required | claim_approval_required | yes | 4266.4 |
| HO-07 | refund | refund_denied | refund_denied | yes | 3773.0 |
| HO-08 | general | general_answer | general_answer | yes | 1910.0 |
| HO-09 | general | general_answer | general_answer | yes | 1799.9 |
| HO-10 | security | blocked_input | blocked_input | yes | 0.1 |
| HO-11 | security | blocked_input | blocked_input | yes | 0.1 |
| HO-12 | refund | refund_denied | refund_denied | yes | 3745.3 |
