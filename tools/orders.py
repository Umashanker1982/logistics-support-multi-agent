"""Read-only lookup tools over the simulated order, shipping, return, and refund systems."""
from __future__ import annotations

from tools.backend import Backend
from tools.types import ToolResult


def _missing(order_id: str) -> ToolResult:
    return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")


def get_order(backend: Backend, order_id: str) -> ToolResult:
    """Return order header, items, total, and insurance details."""
    order = backend.orders.get(order_id)
    if order is None:
        return _missing(order_id)
    total = round(sum(item["price"] for item in order["items"]), 2)
    return ToolResult.success(
        {
            "order_id": order_id,
            "customer_id": order["customer_id"],
            "status": order["status"],
            "delivered_on": order["delivered_on"],
            "items": order["items"],
            "total": total,
            "insurance": order["insurance"],
        },
        source=order_id,
    )


def get_shipment(backend: Backend, order_id: str) -> ToolResult:
    """Return carrier tracking status for an order."""
    if order_id not in backend.orders:
        return _missing(order_id)
    shipment = backend.shipments.get(order_id)
    if shipment is None:
        return ToolResult.failure("NO_SHIPMENT", f"No carrier tracking is on file for {order_id}.")
    return ToolResult.success({"order_id": order_id, **shipment}, source=order_id)


def get_return_status(backend: Backend, order_id: str) -> ToolResult:
    """Return whether the warehouse has received a return for this order."""
    if order_id not in backend.orders:
        return _missing(order_id)
    ret = backend.returns.get(order_id)
    return ToolResult.success(
        {"order_id": order_id, "returned": ret is not None, **(ret or {})},
        source=order_id,
    )


def get_refund_status(backend: Backend, order_id: str) -> ToolResult:
    """Return any refund already issued for this order."""
    if order_id not in backend.orders:
        return _missing(order_id)
    refund = backend.refunds.get(order_id)
    return ToolResult.success(
        {"order_id": order_id, "refunded": refund is not None, **(refund or {})},
        source=refund["refund_id"] if refund else order_id,
    )


def get_claim_status(backend: Backend, order_id: str) -> ToolResult:
    """Return any insurance claim already filed for this order."""
    if order_id not in backend.orders:
        return _missing(order_id)
    claim = backend.claims.get(order_id)
    return ToolResult.success(
        {"order_id": order_id, "claimed": claim is not None, **(claim or {})},
        source=claim["claim_id"] if claim else order_id,
    )
