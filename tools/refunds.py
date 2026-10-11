"""Refund eligibility (read-only) and refund execution (write) tools."""
from __future__ import annotations

from datetime import date

from tools.backend import Backend
from tools.types import ToolResult


def check_refund_eligibility(
    backend: Backend,
    order_id: str,
    approval_threshold: float,
    window_days: int,
) -> ToolResult:
    """Apply refund rules R-1..R-5 and return a structured decision.

    The recorded return reason from the warehouse is the source of truth, not the
    reason the customer typed, so a customer cannot claim "defective" to turn a
    change-of-mind return into a cash refund.
    """
    order = backend.orders.get(order_id)
    if order is None:
        return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")
    amount = round(sum(item["price"] for item in order["items"]), 2)

    def decision(eligible: bool, code: str, rules: list[str], explanation: str, **extra) -> ToolResult:
        return ToolResult.success(
            {
                "order_id": order_id,
                "eligible": eligible,
                "decision": code,
                "amount": amount if eligible else 0.0,
                "rule_ids": rules,
                "explanation": explanation,
                "requires_approval": False,
                **extra,
            },
            source="POLICY " + rules[0],
        )

    existing = backend.refunds.get(order_id)
    if existing:
        return decision(False, "already_refunded", ["R-5"],
                        f"Refund {existing['refund_id']} for ${existing['amount']:.2f} was issued on {existing['issued_on']}.",
                        existing_refund=existing)

    ret = backend.returns.get(order_id)
    if ret is None:
        return decision(False, "not_returned", ["R-1"],
                        "The item has not been returned to our warehouse yet.")

    if ret["reason"] == "change_of_mind":
        return decision(False, "store_credit_only", ["R-3"],
                        "This was a change-of-mind return, which is refunded as store credit.")

    days = (backend.as_of - date.fromisoformat(ret["received_on"])).days
    if days > window_days:
        return decision(False, "outside_window", ["R-2"],
                        f"The return was received {days} days ago, beyond the {window_days}-day refund window.",
                        days_since_return=days)

    needs_approval = amount > approval_threshold
    rules = ["R-1", "R-2", "R-4"] if needs_approval else ["R-1", "R-2"]
    result = decision(True, "eligible", rules,
                      f"Return received {days} days ago for reason '{ret['reason']}'.",
                      days_since_return=days)
    result.data["amount"] = amount
    result.data["requires_approval"] = needs_approval
    return result


def issue_refund(backend: Backend, order_id: str, amount: float, approval_id: str | None = None) -> ToolResult:
    """Create a refund record. Idempotent per order: a second refund is rejected (R-5)."""
    if order_id not in backend.orders:
        return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")
    if order_id in backend.refunds:
        return ToolResult.failure("DUPLICATE_REFUND", f"Order {order_id} has already been refunded.")
    if amount <= 0:
        return ToolResult.failure("INVALID_AMOUNT", "Refund amount must be positive.")
    refund_id = backend.next_id("refund", "RF")
    row = {
        "refund_id": refund_id,
        "amount": round(amount, 2),
        "issued_on": backend.as_of.isoformat(),
        "status": "issued",
        "approval_id": approval_id,
    }
    backend.record("refunds", order_id, row)
    return ToolResult.success({"order_id": order_id, **row}, source=refund_id)
