"""Support-ticket tool, used for escalations, investigations, and pending approvals."""
from __future__ import annotations

from tools.backend import Backend
from tools.types import ToolResult

VALID_CATEGORIES = {"refund_approval", "claim_approval", "lost_parcel", "escalation"}
VALID_PRIORITIES = {"low", "normal", "high"}


def create_ticket(backend: Backend, order_id: str, category: str, summary: str, priority: str = "normal") -> ToolResult:
    if category not in VALID_CATEGORIES:
        return ToolResult.failure("INVALID_CATEGORY", f"Unknown ticket category {category!r}.")
    if priority not in VALID_PRIORITIES:
        return ToolResult.failure("INVALID_PRIORITY", f"Unknown priority {priority!r}.")
    # Idempotent: reuse an open ticket for the same order and category instead of duplicating it.
    for existing in backend.tickets.values():
        if existing["order_id"] == order_id and existing["category"] == category and existing["status"] == "open":
            return ToolResult.success({**existing, "reused": True}, source=existing["ticket_id"])
    ticket_id = backend.next_id("ticket", "TCK")
    row = {
        "ticket_id": ticket_id,
        "order_id": order_id,
        "category": category,
        "summary": summary[:300],
        "priority": priority,
        "opened_on": backend.as_of.isoformat(),
        "status": "open",
    }
    backend.record("tickets", ticket_id, row)
    return ToolResult.success(row, source=ticket_id)
