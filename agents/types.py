"""Typed messages passed between agents. Each agent takes and returns one of these."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Intent = Literal["refund", "track", "insurance", "general"]
Incident = Literal["damaged", "lost", "defective", "change_of_mind", "wrong_item", "late"]


@dataclass
class IntentResult:
    """Output of the intent agent."""

    intent: str                      # refund | track | insurance | general
    order_id: str | None = None
    order_id_source: str | None = None   # "message" or "memory"
    incident: str | None = None
    faq_topic: str | None = None     # for general questions
    confidence: float = 0.0
    classifier: str = "rules"        # "rules" or "claude"


@dataclass
class RouteDecision:
    """Output of the router: which workflow and specialist agents handle the request."""

    intent: str
    workflow: str
    agents: list[str]
    requires_order: bool


@dataclass
class Facts:
    """Output of the lookup agent: everything gathered from backends, with citations."""

    order: dict[str, Any] | None = None
    shipment: dict[str, Any] | None = None
    return_status: dict[str, Any] | None = None
    refund: dict[str, Any] | None = None
    claim: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    gathered_by: str = "rules"


@dataclass
class PolicyDecision:
    """Output of the policy agent."""

    kind: str                        # refund | claim | tracking
    eligible: bool
    decision: str                    # e.g. eligible, not_returned, no_coverage, investigation_required
    amount: float = 0.0
    requires_approval: bool = False
    rule_ids: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class ExecutionResult:
    """Output of the execution agent."""

    outcome: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    approval_id: str | None = None


@dataclass
class AgentResponse:
    """What the orchestrator returns to the caller (CLI, API, benchmark)."""

    trace_id: str
    reply: str
    intent: str | None
    route: str | None
    outcome: str
    order_id: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    approval_id: str | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    llm_calls: int = 0
    guardrail_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
