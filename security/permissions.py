"""Permission boundaries: which agent may call which tool.

This is least privilege applied to agents. The lookup agent can only read, the
policy agent can only evaluate rules, and only the execution agent can move
money or open tickets. The intent and response agents get no tools at all, so a
prompt-injected model output can never reach a write tool directly.
"""
from __future__ import annotations

READ_TOOLS = {"get_order", "get_shipment", "get_return_status", "get_refund_status", "get_claim_status"}
POLICY_TOOLS = {"check_refund_eligibility", "check_claim_eligibility"}
WRITE_TOOLS = {"issue_refund", "file_claim", "create_ticket"}

AGENT_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "intent_agent": frozenset(),
    "lookup_agent": frozenset(READ_TOOLS),
    "policy_agent": frozenset(POLICY_TOOLS),
    "execution_agent": frozenset(WRITE_TOOLS),
    "response_agent": frozenset(),
}


def is_allowed(agent: str, tool: str) -> bool:
    return tool in AGENT_TOOL_ALLOWLIST.get(agent, frozenset())
