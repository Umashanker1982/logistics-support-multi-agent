"""Operational summary computed from ops/logs/traces.jsonl.

This is the local stand-in for a metrics dashboard: the same numbers would be
exported as Prometheus/CloudWatch metrics in production (see ops/observability.md).
"""
from __future__ import annotations

import json
from collections import Counter

from config import load_settings
from security.approvals import ApprovalQueue

ERROR_OUTCOMES = {"system_error", "action_failed"}


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summarize() -> str:
    s = load_settings()
    path = s.log_dir / "traces.jsonl"
    if not path.exists():
        return "No traces yet. Run `python main.py demo` first."
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    customer = [r for r in records if r.get("route") != "human_approval"]
    lat = [r["latency_ms"] for r in customer]
    outcomes = Counter(r["outcome"] for r in customer)
    intents = Counter(r.get("intent") or "-" for r in customer)
    errors = sum(outcomes[o] for o in ERROR_OUTCOMES)
    blocked = outcomes["blocked_input"]
    denied = outcomes["access_denied"]
    llm_failures = sum(1 for r in customer for c in r.get("llm_calls", []) if not c.get("ok"))
    rejected_drafts = sum(1 for r in customer for e in r.get("events", [])
                          if e["name"] == "response_rejected_by_output_guardrail")
    pending = len(ApprovalQueue(s.approvals_file).list("pending"))
    n = max(len(customer), 1)
    lines = [
        f"Requests: {len(customer)}",
        f"Error rate: {errors / n:.1%}  (alert if > 2% over 15 min)",
        f"Latency p50/p95: {percentile(lat, 0.5):.0f} / {percentile(lat, 0.95):.0f} ms",
        f"Total model cost: ${sum(r['cost_usd'] for r in customer):.4f}",
        f"Blocked inputs (guardrail): {blocked} | Ownership denials: {denied} | LLM failures (fell back): {llm_failures}"
        f" | Drafts rejected by output guardrail: {rejected_drafts}",
        f"Pending human approvals: {pending}  (alert if any older than 4 business hours)",
        "Intents:  " + ", ".join(f"{k}={v}" for k, v in intents.most_common()),
        "Outcomes: " + ", ".join(f"{k}={v}" for k, v in outcomes.most_common()),
    ]
    return "\n".join(lines)
