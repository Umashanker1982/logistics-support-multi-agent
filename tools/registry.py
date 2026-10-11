"""The single gateway every agent uses to call a tool.

Agents never import tool functions directly. They call ToolRegistry.call(), which
enforces, in order:

1. Permission boundary  -- the calling agent must be allowed to use the tool.
2. Input validation     -- order ids must match the ORD-#### format.
3. Ownership guardrail  -- the order must belong to the authenticated customer (S-1).
4. Audit reason         -- write tools must state why they are being called.
5. Error isolation      -- unexpected exceptions become a TOOL_ERROR result.
6. Logging              -- every call lands in the audit log and the request trace.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from config import Settings
from ops.observability import Trace
from security.permissions import WRITE_TOOLS, is_allowed
from tools import insurance, orders, refunds, tickets
from tools.audit import AuditLog
from tools.backend import Backend
from tools.types import ToolResult

ORDER_ID_FORMAT = re.compile(r"^ORD-\d{4}$")


class ToolRegistry:
    def __init__(self, backend: Backend, audit: AuditLog, settings: Settings):
        self.backend = backend
        self.audit = audit
        self.settings = settings
        s = settings
        self._tools: dict[str, Callable[..., ToolResult]] = {
            "get_order": lambda order_id: orders.get_order(backend, order_id),
            "get_shipment": lambda order_id: orders.get_shipment(backend, order_id),
            "get_return_status": lambda order_id: orders.get_return_status(backend, order_id),
            "get_refund_status": lambda order_id: orders.get_refund_status(backend, order_id),
            "get_claim_status": lambda order_id: orders.get_claim_status(backend, order_id),
            "check_refund_eligibility": lambda order_id: refunds.check_refund_eligibility(
                backend, order_id, s.refund_approval_threshold, s.refund_window_days),
            "check_claim_eligibility": lambda order_id: insurance.check_claim_eligibility(
                backend, order_id, s.refund_approval_threshold),
            "issue_refund": lambda order_id, amount, approval_id=None: refunds.issue_refund(
                backend, order_id, amount, approval_id),
            "file_claim": lambda order_id, payout, incident, approval_id=None: insurance.file_claim(
                backend, order_id, payout, incident, approval_id),
            "create_ticket": lambda order_id, category, summary, priority="normal": tickets.create_ticket(
                backend, order_id, category, summary, priority),
        }

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def owns(self, customer_id: str, order_id: str) -> bool:
        order = self.backend.orders.get(order_id)
        return order is not None and order["customer_id"] == customer_id

    def call(self, *, agent: str, tool: str, trace: Trace, customer_id: str, reason: str, **kwargs: Any) -> ToolResult:
        start = time.perf_counter()
        result = self._guarded_call(agent, tool, customer_id, reason, kwargs)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        self.audit.log_action(
            trace_id=trace.trace_id, customer_id=customer_id, actor=agent, tool=tool,
            args=kwargs, reason=reason, ok=result.ok, error=result.error, source=result.source,
        )
        trace.record_tool(agent=agent, tool=tool, args=kwargs, reason=reason, ok=result.ok,
                          error=result.error, source=result.source, duration_ms=duration_ms)
        return result

    def _guarded_call(self, agent: str, tool: str, customer_id: str, reason: str, kwargs: dict[str, Any]) -> ToolResult:
        if tool not in self._tools:
            return ToolResult.failure("UNKNOWN_TOOL", f"Tool {tool!r} does not exist.")
        if not is_allowed(agent, tool):
            return ToolResult.failure("PERMISSION_DENIED", f"{agent} is not permitted to call {tool}.")
        order_id = kwargs.get("order_id")
        if not isinstance(order_id, str) or not ORDER_ID_FORMAT.match(order_id):
            return ToolResult.failure("INVALID_INPUT", "order_id must look like ORD-1234.")
        if order_id not in self.backend.orders:
            return ToolResult.failure("ORDER_NOT_FOUND", f"No order found with id {order_id}.")
        if not self.owns(customer_id, order_id):
            return ToolResult.failure("ACCESS_DENIED", f"Order {order_id} does not belong to this customer.")
        if tool in WRITE_TOOLS and not reason.strip():
            return ToolResult.failure("MISSING_AUDIT_REASON", "Write tools require an audit reason.")
        try:
            return self._tools[tool](**kwargs)
        except TypeError as exc:
            return ToolResult.failure("INVALID_INPUT", f"Bad arguments for {tool}: {exc}")
        except Exception as exc:  # never let a backend failure crash the request
            return ToolResult.failure("TOOL_ERROR", f"{tool} failed: {type(exc).__name__}")
