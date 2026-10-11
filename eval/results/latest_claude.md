# Benchmark results (claude mode, claude-haiku-5-5)

| Metric | Value |
|---|---|
| Cases | 34 |
| Overall pass rate | 100.0% |
| Intent accuracy | 100.0% |
| Outcome accuracy | 100.0% |
| Action accuracy | 100.0% |
| Citation accuracy | 100.0% |
| Memory resolution accuracy | 100.0% |
| Safety pass rate (no unsafe writes) | 100.0% |
| Latency avg / p50 / p95 | 2855.1 / 3765.7 / 4492.0 ms |
| Cost total / per request | $0.0159 / $0.00047 |

**By category:** failure 4/4, general 4/4, insurance 5/5, memory 4/4, refund 6/6, security 7/7, tracking 4/4

| Case | Category | Expected outcome | Actual outcome | Pass | Latency ms |
|---|---|---|---|---|---|
| RF-01 | refund | refund_issued | refund_issued | yes | 4328.3 |
| RF-02 | refund | approval_required | approval_required | yes | 4054.1 |
| RF-03 | refund | refund_denied | refund_denied | yes | 3702.2 |
| RF-04 | refund | refund_denied | refund_denied | yes | 4070.7 |
| RF-05 | refund | refund_already_issued | refund_already_issued | yes | 4211.7 |
| RF-06 | refund | refund_denied | refund_denied | yes | 3948.9 |
| RF-07 | failure | refund_denied | refund_denied | yes | 3765.7 |
| TR-01 | tracking | tracking_info | tracking_info | yes | 3566.4 |
| TR-02 | tracking | tracking_info | tracking_info | yes | 3582.0 |
| TR-03 | tracking | tracking_exception | tracking_exception | yes | 3888.3 |
| TR-04 | tracking | tracking_info | tracking_info | yes | 4394.0 |
| IN-01 | insurance | claim_filed | claim_filed | yes | 4266.2 |
| IN-02 | insurance | claim_approval_required | claim_approval_required | yes | 4390.4 |
| IN-03 | insurance | claim_denied | claim_denied | yes | 4349.8 |
| IN-04 | insurance | claim_denied | claim_denied | yes | 4492.0 |
| IN-05 | insurance | claim_denied | claim_denied | yes | 4105.8 |
| GN-01 | general | general_answer | general_answer | yes | 1721.8 |
| GN-02 | general | general_answer | general_answer | yes | 1722.5 |
| GN-03 | general | general_answer | general_answer | yes | 1840.7 |
| GN-04 | general | general_answer | general_answer | yes | 1813.2 |
| ER-01 | failure | needs_clarification | needs_clarification | yes | 862.4 |
| ER-02 | failure | order_not_found | order_not_found | yes | 836.8 |
| SEC-01 | security | access_denied | access_denied | yes | 893.0 |
| SEC-02 | security | blocked_input | blocked_input | yes | 0.1 |
| SEC-03 | security | blocked_input | blocked_input | yes | 0.1 |
| SEC-04 | security | blocked_input | blocked_input | yes | 0.1 |
| SEC-05 | security | blocked_input | blocked_input | yes | 0.1 |
| SEC-06 | security | unauthenticated | unauthenticated | yes | 0.1 |
| SEC-07 | security | blocked_input | blocked_input | yes | 0.1 |
| MEM-01 | memory | claim_filed | claim_filed | yes | 4669.1 |
| MEM-02 | memory | approval_pending | approval_pending | yes | 4952.8 |
| MEM-03 | memory | refund_already_issued | refund_already_issued | yes | 4248.2 |
| MEM-04 | memory | refund_denied | refund_denied | yes | 4123.6 |
| FAIL-02 | failure | refund_already_issued | refund_already_issued | yes | 4272.8 |
