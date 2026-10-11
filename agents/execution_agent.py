"""Execution agent: the only agent allowed to change anything (refunds, claims, tickets).

It acts only on a PolicyDecision from the policy agent, never on model output, and:
* pays out directly only when the decision is eligible AND under the approval threshold;
* above the threshold, opens a ticket plus a human approval request and stops;
* executes an approved request later, citing the approval id (see execute_approved).
"""
from __future__ import annotations

from typing import Any

from agents.types import ExecutionResult, PolicyDecision
from ops.observability import Trace
from security.approvals import APPROVED, ApprovalQueue
from tools.registry import ToolRegistry

DENIED_OUTCOME = {
    "refund": "refund_denied",
    "claim": "claim_denied",
}


class ExecutionAgent:
    name = "execution_agent"

    def __init__(self, registry: ToolRegistry, approvals: ApprovalQueue):
        self.registry = registry
        self.approvals = approvals

    def _call(self, tool: str, trace: Trace, customer_id: str, reason: str, **kwargs: Any):
        return self.registry.call(agent=self.name, tool=tool, trace=trace, customer_id=customer_id,
                                  reason=reason, **kwargs)

    def _request_approval(self, decision: PolicyDecision, order_id: str, customer_id: str,
                          incident: str, trace: Trace) -> ExecutionResult:
        category = "refund_approval" if decision.kind == "refund" else "claim_approval"
        ticket = self._call("create_ticket", trace, customer_id,
                            reason=f"{decision.kind} of ${decision.amount:.2f} exceeds approval threshold",
                            order_id=order_id, category=category, priority="high",
                            summary=f"Approve {decision.kind} of ${decision.amount:.2f} for {order_id} "
                                    f"(rules {', '.join(decision.rule_ids)})")
        approval = self.approvals.request(kind=decision.kind, order_id=order_id, customer_id=customer_id,
                                          amount=decision.amount, rule_ids=decision.rule_ids,
                                          trace_id=trace.trace_id, incident=incident,
                                          ticket_id=ticket.data.get("ticket_id") if ticket.ok else None)
        trace.event("human_approval_requested", approval_id=approval["approval_id"], amount=decision.amount)
        actions = [{"type": "approval_requested", "ref": approval["approval_id"]}]
        if ticket.ok:
            actions.append({"type": "ticket_created", "ref": ticket.data["ticket_id"]})
        outcome = "approval_required" if decision.kind == "refund" else "claim_approval_required"
        return ExecutionResult(outcome=outcome, actions=actions, approval_id=approval["approval_id"])

    def _pay(self, kind: str, order_id: str, amount: float, customer_id: str, incident: str,
             trace: Trace, approval_id: str | None) -> ExecutionResult:
        reason = f"policy-approved {kind}" + (f" under {approval_id}" if approval_id else " below threshold")
        if kind == "refund":
            res = self._call("issue_refund", trace, customer_id, reason, order_id=order_id,
                             amount=amount, approval_id=approval_id)
            ok_outcome, ref_key, action = "refund_issued", "refund_id", "refund_issued"
        else:
            res = self._call("file_claim", trace, customer_id, reason, order_id=order_id,
                             payout=amount, incident=incident or "damaged", approval_id=approval_id)
            ok_outcome, ref_key, action = "claim_filed", "claim_id", "claim_filed"
        if not res.ok:
            trace.event("execution_failed", tool=action, error=res.error)
            return ExecutionResult(outcome="action_failed", actions=[{"type": "error", "ref": res.error}])
        return ExecutionResult(outcome=ok_outcome, actions=[{"type": action, "ref": res.data[ref_key]}],
                               approval_id=approval_id)

    def run(self, decision: PolicyDecision, order_id: str, customer_id: str,
            incident: str | None, trace: Trace) -> ExecutionResult:
        if decision.decision.startswith("error:"):
            return ExecutionResult(outcome="action_failed")
        if decision.kind == "tracking":
            if decision.decision != "investigation_required":
                return ExecutionResult(outcome="tracking_info")
            ticket = self._call("create_ticket", trace, customer_id, reason="carrier exception (rule T-1)",
                                order_id=order_id, category="lost_parcel", priority="high",
                                summary=f"Investigate carrier exception for {order_id}")
            actions = [{"type": "ticket_created", "ref": ticket.data["ticket_id"]}] if ticket.ok else []
            return ExecutionResult(outcome="tracking_exception", actions=actions)

        if not decision.eligible:
            if decision.decision == "already_refunded":
                return ExecutionResult(outcome="refund_already_issued")
            if decision.decision == "claim_exists":
                return ExecutionResult(outcome="claim_already_filed")
            return ExecutionResult(outcome=DENIED_OUTCOME[decision.kind])

        if decision.requires_approval:
            return self._request_approval(decision, order_id, customer_id, incident or "", trace)
        return self._pay(decision.kind, order_id, decision.amount, customer_id, incident or "", trace, None)

    def execute_approved(self, approval_id: str, trace: Trace) -> ExecutionResult:
        """Carry out a request a human has approved. Refuses anything not in APPROVED state."""
        item = self.approvals.get(approval_id)
        if item is None or item["status"] != APPROVED:
            return ExecutionResult(outcome="approval_not_executable", approval_id=approval_id)
        result = self._pay(item["kind"], item["order_id"], item["amount"], item["customer_id"],
                           item.get("incident", ""), trace, approval_id)
        if result.outcome in {"refund_issued", "claim_filed"}:
            self.approvals.mark_executed(approval_id, result.actions[0]["ref"])
        return result
