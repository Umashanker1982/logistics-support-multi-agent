"""Deterministic router: maps a classified intent to a workflow of specialist agents.

Why deterministic? The intent set is small and closed (4 intents), every route
must be explainable to auditors, and a lookup table cannot be prompt-injected.
The "intelligence" lives in the intent agent; the router itself is a table that
is trivially testable. A learned router would only pay off with many more intents.
"""
from __future__ import annotations

from agents.types import IntentResult, RouteDecision

# intent -> (workflow name, specialist agents in order, needs an order id?)
ROUTES: dict[str, tuple[str, list[str], bool]] = {
    "refund": ("refund_workflow",
               ["lookup_agent", "policy_agent", "execution_agent", "response_agent"], True),
    "track": ("tracking_workflow",
              ["lookup_agent", "policy_agent", "execution_agent", "response_agent"], True),
    "insurance": ("insurance_workflow",
                  ["lookup_agent", "policy_agent", "execution_agent", "response_agent"], True),
    "general": ("general_workflow", ["general_agent"], False),
}


def route(intent: IntentResult) -> RouteDecision:
    workflow, agents, needs_order = ROUTES.get(intent.intent, ROUTES["general"])
    return RouteDecision(intent=intent.intent, workflow=workflow, agents=list(agents), requires_order=needs_order)
