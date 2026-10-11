"""Shipping-insurance eligibility (read-only) and claim filing (write) tools."""
from __future__ import annotations

from datetime import date

from tools.backend import Backend
from tools.types import ToolResult

CLAIM_WINDOW_DAYS = 60


def check_claim_eligibility(backend: Backend, order_id: str, approval_threshold: float) -> ToolResult:
    """Apply insurance rules I-1..I-5 and return a structured decision."""
    order = backend.orders.get(order_id)
    if order is None:
        return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")
    value = round(sum(item["price"] for item in order["items"]), 2)
    coverage = order.get("insurance")

    def decision(eligible: bool, code: str, rules: list[str], explanation: str, payout: float = 0.0) -> ToolResult:
        return ToolResult.success(
            {
                "order_id": order_id,
                "eligible": eligible,
                "decision": code,
                "amount": payout,
                "rule_ids": rules,
                "explanation": explanation,
                "requires_approval": eligible and payout > approval_threshold,
            },
            source="POLICY " + rules[0],
        )

    if not coverage:
        return decision(False, "no_coverage", ["I-1"], "This order was shipped without insurance.")

    if order_id in backend.claims:
        claim = backend.claims[order_id]
        return decision(False, "claim_exists", ["I-1"],
                        f"Claim {claim['claim_id']} is already open for this order (status: {claim['status']}).")

    if order["status"] == "in_transit":
        return decision(False, "not_yet_delivered", ["I-5"],
                        "The parcel is still in transit, so a claim cannot be filed yet.")

    if order["delivered_on"]:
        days = (backend.as_of - date.fromisoformat(order["delivered_on"])).days
        if days > CLAIM_WINDOW_DAYS:
            return decision(False, "outside_window", ["I-3"],
                            f"Delivered {days} days ago, beyond the {CLAIM_WINDOW_DAYS}-day claim window.")

    payout = round(min(value, float(coverage["coverage_limit"])), 2)
    rules = ["I-1", "I-2", "I-3"] + (["I-4"] if payout > approval_threshold else [])
    return decision(True, "eligible", rules,
                    f"Covered up to ${coverage['coverage_limit']:.2f}; item value ${value:.2f}.", payout)


def file_claim(backend: Backend, order_id: str, payout: float, incident: str, approval_id: str | None = None) -> ToolResult:
    """Open an insurance claim. One claim per order."""
    if order_id not in backend.orders:
        return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")
    if order_id in backend.claims:
        return ToolResult.failure("DUPLICATE_CLAIM", f"A claim already exists for {order_id}.")
    claim_id = backend.next_id("claim", "CLM")
    row = {
        "claim_id": claim_id,
        "payout": round(payout, 2),
        "incident": incident,
        "filed_on": backend.as_of.isoformat(),
        "status": "approved_for_payout",
        "approval_id": approval_id,
    }
    backend.record("claims", order_id, row)
    return ToolResult.success({"order_id": order_id, **row}, source=claim_id)
