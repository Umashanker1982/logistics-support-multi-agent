"""Lookup agent: gathers order, shipment, return, refund, and claim facts through read-only tools.

In Claude mode the model chooses which read tools to call (an agentic tool-use
loop, capped at a few rounds). The agent then verifies the required facts were
actually gathered and fills any gap deterministically -- "trust, but verify" --
so the downstream policy decision never depends on whether the model remembered
to call a tool. In offline mode it runs a fixed lookup plan per intent.
"""
from __future__ import annotations

import json
from typing import Any

from agents.llm import LLMClient
from agents.prompts import LOOKUP_SYSTEM
from agents.types import Facts
from ops.observability import Trace
from tools.registry import ToolRegistry

# intent -> (tool name, Facts attribute) pairs that MUST be gathered
REQUIRED: dict[str, list[tuple[str, str]]] = {
    "refund": [("get_order", "order"), ("get_return_status", "return_status"), ("get_refund_status", "refund")],
    "track": [("get_order", "order"), ("get_shipment", "shipment")],
    "insurance": [("get_order", "order"), ("get_shipment", "shipment"), ("get_claim_status", "claim")],
}
TOOL_TO_FIELD = {"get_order": "order", "get_shipment": "shipment", "get_return_status": "return_status",
                 "get_refund_status": "refund", "get_claim_status": "claim"}
MAX_ROUNDS = 4


def _tool_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object",
                         "properties": {"order_id": {"type": "string", "description": "Order id like ORD-1234"}},
                         "required": ["order_id"]},
    }


CLAUDE_TOOLS = [
    _tool_schema("get_order", "Order status, delivery date, items, total, and insurance."),
    _tool_schema("get_shipment", "Carrier, tracking number, latest scan, and ETA."),
    _tool_schema("get_return_status", "Whether the warehouse received a return, when, and why."),
    _tool_schema("get_refund_status", "Any refund already issued for the order."),
    _tool_schema("get_claim_status", "Any insurance claim already filed for the order."),
]


class LookupAgent:
    name = "lookup_agent"

    def __init__(self, registry: ToolRegistry, llm: LLMClient):
        self.registry = registry
        self.llm = llm

    def _call(self, tool: str, order_id: str, customer_id: str, trace: Trace, facts: Facts, reason: str) -> None:
        res = self.registry.call(agent=self.name, tool=tool, trace=trace, customer_id=customer_id,
                                 reason=reason, order_id=order_id)
        if res.ok:
            setattr(facts, TOOL_TO_FIELD[tool], res.data)
            if res.source and res.source not in facts.sources:
                facts.sources.append(res.source)
        elif res.error not in {"NO_SHIPMENT"}:
            facts.errors.append(res.error or "UNKNOWN")

    def _claude_loop(self, intent: str, order_id: str, customer_id: str, trace: Trace, facts: Facts) -> None:
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": f"Intent: {intent}\nOrder id: {order_id}\nGather the facts needed for this intent.",
        }]
        for _ in range(MAX_ROUNDS):
            resp = self.llm.create(trace=trace, agent=self.name, system=LOOKUP_SYSTEM,
                                   messages=messages, tools=CLAUDE_TOOLS, max_tokens=300)
            if resp is None or resp.stop_reason != "tool_use":
                return
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                requested = str((block.input or {}).get("order_id", "")).upper()
                if block.name not in TOOL_TO_FIELD or requested != order_id:
                    payload = {"ok": False, "error": "OUT_OF_SCOPE", "message": f"Only {order_id} may be looked up."}
                    trace.event("lookup_out_of_scope_call", tool=block.name, requested=requested[:20])
                else:
                    self._call(block.name, order_id, customer_id, trace, facts, reason=f"model-selected lookup for {intent}")
                    payload = {"ok": True, "data": getattr(facts, TOOL_TO_FIELD[block.name])}
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(payload, default=str)})
            messages.append({"role": "user", "content": results})
        facts.gathered_by = "claude"

    def verify_access(self, order_id: str, customer_id: str, trace: Trace):
        """First call of every order workflow: confirms the order exists and belongs to the caller."""
        return self.registry.call(agent=self.name, tool="get_order", trace=trace, customer_id=customer_id,
                                  reason="verify order exists and belongs to customer", order_id=order_id)

    def run(self, intent: str, order_id: str, customer_id: str, trace: Trace,
            order: dict[str, Any] | None = None) -> Facts:
        facts = Facts(order=order, sources=[order_id] if order else [])
        if self.llm.enabled:
            self._claude_loop(intent, order_id, customer_id, trace, facts)
            if any(getattr(facts, f) is not None for f in TOOL_TO_FIELD.values()):
                facts.gathered_by = "claude"
        # Verify: fill anything the model skipped (or everything, in offline mode).
        for tool, field in REQUIRED.get(intent, []):
            if getattr(facts, field) is None:
                if facts.gathered_by == "claude":
                    trace.event("lookup_gap_filled", tool=tool)
                self._call(tool, order_id, customer_id, trace, facts, reason=f"required lookup for {intent}")
        return facts
