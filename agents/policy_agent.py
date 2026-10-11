"""Policy agent: turns gathered facts into a structured, rule-cited decision.

Deliberately deterministic (no model): eligibility decides whether money moves,
so it must be exact, reproducible, unit-tested, and explainable by rule id. The
rules themselves live in the eligibility tools (tools/refunds.py, tools/insurance.py)
and their text in tools/data/policies.json.
"""
from __future__ import annotations

from agents.types import Facts, PolicyDecision
from ops.observability import Trace
from tools.registry import ToolRegistry


class PolicyAgent:
    name = "policy_agent"

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def _check(self, tool: str, kind: str, order_id: str, customer_id: str, trace: Trace) -> PolicyDecision:
        res = self.registry.call(agent=self.name, tool=tool, trace=trace, customer_id=customer_id,
                                 reason=f"evaluate {kind} policy", order_id=order_id)
        if not res.ok:
            return PolicyDecision(kind=kind, eligible=False, decision=f"error:{res.error}", explanation=res.message)
        d = res.data
        return PolicyDecision(kind=kind, eligible=d["eligible"], decision=d["decision"], amount=d["amount"],
                              requires_approval=d["requires_approval"], rule_ids=d["rule_ids"],
                              explanation=d["explanation"])

    def run(self, intent: str, order_id: str, customer_id: str, facts: Facts, trace: Trace) -> PolicyDecision:
        if intent == "refund":
            return self._check("check_refund_eligibility", "refund", order_id, customer_id, trace)
        if intent == "insurance":
            return self._check("check_claim_eligibility", "claim", order_id, customer_id, trace)
        # tracking: the only policy question is whether the shipment needs an investigation (T-1)
        shipment = facts.shipment or {}
        if shipment.get("status") == "exception":
            return PolicyDecision(kind="tracking", eligible=True, decision="investigation_required",
                                  rule_ids=["T-1"], explanation="The carrier reported a delivery exception.")
        return PolicyDecision(kind="tracking", eligible=False, decision="info_only",
                              explanation="No action needed; share tracking status.")
