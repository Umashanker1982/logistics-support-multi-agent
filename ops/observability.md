# Observability

Every customer message produces **one trace record**, a JSON line in `ops/logs/traces.jsonl`. Every tool call also produces an **audit entry** in `ops/logs/audit.jsonl`. The two join on `trace_id`.

## Trace schema

| Field | Meaning |
|---|---|
| `trace_id`, `session_id`, `customer_id`, `started_at` | Correlation |
| `message` | Customer text, truncated to 500 characters and **PII-redacted** |
| `intent`, `route`, `order_id`, `outcome`, `actions`, `approval_id` | What happened |
| `guardrail_flags` | e.g. `override_instructions`, `ownership_check_failed` |
| `reply` | What the customer saw |
| `latency_ms`, `cost_usd`, `llm_tokens` | Performance and spend |
| `spans[]` | One per step: `input_guardrail`, `intent_agent`, `router`, `lookup_agent`, `approval_check`, `policy_agent`, `execution_agent`, `response_agent`. Each has `start_ms`, `duration_ms`, `status`, and attributes (e.g. the policy decision and rule ids) |
| `tool_calls[]` | Agent, tool, args, audit reason, ok/error, citation source, duration |
| `llm_calls[]` | Agent, model, input and output tokens, cost, latency, stop reason, ok/error |
| `events[]` | Notable moments: `llm_fallback`, `human_approval_requested`, `response_rejected_by_output_guardrail`, `security_access_denied`, `lookup_out_of_scope_call`, `intent_discarded_unseen_order_id` |

Example, abridged from a real run:

```json
{"trace_id": "tr-5709f0f8baed", "intent": "refund", "route": "refund_workflow", "order_id": "ORD-1002",
 "outcome": "refund_issued", "actions": [{"type": "refund_issued", "ref": "RF-6001"}], "latency_ms": 3.2,
 "spans": [{"name": "input_guardrail", "duration_ms": 0.04}, {"name": "intent_agent", "attrs": {"intent": "refund", "classifier": "rules"}},
           {"name": "policy_agent", "attrs": {"decision": "eligible", "amount": 45.0, "rule_ids": ["R-1", "R-2"]}}, "..."],
 "tool_calls": [{"agent": "execution_agent", "tool": "issue_refund", "reason": "policy-approved refund below threshold", "ok": true}]}
```

To inspect locally:
- `python main.py stats` for the summary.
- `python main.py ask "..." --verbose` to see the trace id.
- Search `traces.jsonl` for that trace id to see the full record.

## Live streaming

The same trace is also streamed while it is being built. `Trace` emits `span_start`, `span_end`, `tool`, `llm`, and `event` messages to an optional listener. The web demo forwards them to the browser as server-sent events, so the tracking timeline in the UI is the operational trace itself, shown live.

## Metrics (derived from traces)

| Metric | Source | Alert |
|---|---|---|
| Request rate by intent and outcome | `intent`, `outcome` | – (dashboard) |
| Error rate (`system_error` + `action_failed`) | `outcome` | **Page** if > 2% over 15 min |
| p95 latency | `latency_ms` | Warn if > 6 s over 15 min |
| Model fallback rate | `llm_calls[].ok == false` | Warn if > 5% over 15 min (provider issue) |
| Output-guardrail rejection rate | `events` | Warn if > 3%/day (prompt or model regression) |
| `blocked_input` rate | `outcome` | Warn if > 5%/day (attack or false positives) |
| `access_denied` per customer | `outcome` + `customer_id` | Warn if > 5/hour for one customer (enumeration) |
| Pending approvals age | approval queue | Warn if older than 4 business hours |
| Cost per request / per day | `cost_usd` | Warn if daily spend > 2× 7-day average |

## Production path

The trace format maps directly to OpenTelemetry:
- trace → root span `support.request`;
- each `spans[]` entry → child span;
- `tool_calls` and `llm_calls` → child spans with GenAI semantic-convention attributes (`gen_ai.request.model`, `gen_ai.usage.input_tokens`).

The step is to swap `Tracer.write()` for an OTLP exporter, sending to Grafana Tempo/Loki, Datadog, or Arize Phoenix. The JSON-lines file can also be shipped as-is by Fluent Bit or the CloudWatch agent, with the metrics above built as log-based metrics.
