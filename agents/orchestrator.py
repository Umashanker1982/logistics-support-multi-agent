"""Supervisor / orchestrator.

It never does specialist work itself. For each customer message it:

  1. authenticates the customer and loads session memory
  2. runs the input guardrail
  3. asks the intent agent what the customer wants
  4. asks the router which workflow handles it
  5. delegates to the specialists in order:
        lookup -> (approval check) -> policy -> execution -> response
  6. synthesizes one AgentResponse, updates memory, and closes the trace

Every step is a span in the request trace (ops/observability.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.execution_agent import ExecutionAgent
from agents.general_agent import GeneralAgent
from agents.intent_agent import IntentAgent
from agents.llm import LLMClient
from agents.lookup_agent import LookupAgent
from agents.policy_agent import PolicyAgent
from agents.response_agent import ResponseAgent
from agents.router import route
from agents.types import AgentResponse, ExecutionResult, Facts, IntentResult, PolicyDecision
from config import Settings, load_settings
from memory.session import SessionMemory, SessionStore
from ops.observability import Trace, Tracer
from security.approvals import APPROVED, PENDING, REJECTED, ApprovalQueue
from security.guardrails import validate_input
from tools.audit import AuditLog
from tools.backend import Backend
from tools.registry import ToolRegistry

FIXED_REPLIES = {
    "blocked_input": ("I can't process that request as written. I can help with refunds, parcel tracking, and "
                      "insurance claims for orders on your account."),
    "too_long": "That message is too long for me to process. Please send a shorter description (under 1,000 characters).",
    "empty": "It looks like your message was empty. How can I help with your order?",
    "order_not_found": ("I couldn't find that order on your account. Please check the order number "
                        "(it starts with ORD- followed by four digits)."),
    "needs_clarification": ("I can help with that. Which order is it about? Your order number starts with ORD- "
                            "followed by four digits."),
    "unauthenticated": "Please sign in so I can look up your orders.",
    "system_error": ("Sorry, something went wrong on our side. Your request has been logged and a team member "
                     "will follow up."),
    "action_failed": ("I wasn't able to complete that action automatically, so I've flagged it for a member of "
                      "our support team, who will follow up."),
}


@dataclass
class Components:
    settings: Settings
    backend: Backend
    tracer: Tracer
    audit: AuditLog
    approvals: ApprovalQueue
    sessions: SessionStore
    llm: LLMClient


def build_components(settings: Settings | None = None, *, persistent: bool = True,
                     backend: Backend | None = None, echo: bool = False) -> Components:
    """Wire everything together. persistent=False gives an isolated in-memory system (benchmark, tests)."""
    s = settings or load_settings()
    if backend is None:
        ledger = (s.log_dir.parent / "state" / "ledger.jsonl") if persistent else None
        backend = Backend.from_dir(s.data_dir, ledger_path=ledger)
    return Components(
        settings=s,
        backend=backend,
        tracer=Tracer(s.log_dir if persistent else None, echo=echo),
        audit=AuditLog(s.log_dir if persistent else None),
        approvals=ApprovalQueue(s.approvals_file if persistent else None),
        sessions=SessionStore(s.memory_dir if persistent else None),
        llm=LLMClient(s),
    )


class Orchestrator:
    def __init__(self, components: Components):
        c = components
        self.c = c
        self.registry = ToolRegistry(c.backend, c.audit, c.settings)
        self.intent_agent = IntentAgent(c.llm)
        self.lookup_agent = LookupAgent(self.registry, c.llm)
        self.policy_agent = PolicyAgent(self.registry)
        self.execution_agent = ExecutionAgent(self.registry, c.approvals)
        self.response_agent = ResponseAgent(c.llm, c.backend, c.settings.refund_approval_threshold)
        self.general_agent = GeneralAgent(c.llm, c.backend)

    # ------------------------------------------------------------------
    def handle(self, message: str, *, customer_id: str, session_id: str) -> AgentResponse:
        trace = self.c.tracer.start(session_id, customer_id, message or "")
        state: dict = {"intent": None, "route": None, "order_id": None, "flags": []}
        try:
            return self._handle(message, customer_id, session_id, trace, state)
        except Exception as exc:  # last line of defence: never crash, always trace
            trace.event("unhandled_exception", error=f"{type(exc).__name__}: {exc}")
            return self._finish(trace, None, state, "system_error", FIXED_REPLIES["system_error"])

    def _handle(self, message: str, customer_id: str, session_id: str, trace: Trace, state: dict) -> AgentResponse:
        # 1. identity
        if customer_id not in self.c.backend.customers:
            return self._finish(trace, None, state, "unauthenticated", FIXED_REPLIES["unauthenticated"])
        memory = self.c.sessions.get(session_id, customer_id)

        # 2. input guardrail
        with trace.span("input_guardrail") as attrs:
            check = validate_input(message, self.c.settings.max_input_chars)
            attrs.update(allowed=check.allowed, reason=check.reason, flags=check.flags)
        memory.add_turn("user", check.text)
        if not check.allowed:
            state["flags"] = check.flags or [check.reason]
            key = {"too_long": "too_long", "empty_message": "empty"}.get(check.reason, "blocked_input")
            return self._finish(trace, memory, state, "blocked_input", FIXED_REPLIES[key])

        # 3. intent
        with trace.span("intent_agent") as attrs:
            intent = self.intent_agent.run(check.text, memory, trace)
            attrs.update(intent=intent.intent, order_id=intent.order_id, order_id_source=intent.order_id_source,
                         incident=intent.incident, confidence=intent.confidence, classifier=intent.classifier)
        state["intent"] = intent.intent
        state["order_id"] = intent.order_id

        # 4. route
        with trace.span("router") as attrs:
            decision_route = route(intent)
            attrs.update(workflow=decision_route.workflow, agents=decision_route.agents)
        state["route"] = decision_route.workflow

        if not decision_route.requires_order:
            with trace.span("general_agent"):
                reply, outcome = self.general_agent.run(check.text, intent, trace)
            return self._finish(trace, memory, state, outcome, reply, intent=intent)
        if not intent.order_id:
            return self._finish(trace, memory, state, "needs_clarification", FIXED_REPLIES["needs_clarification"],
                                intent=intent)

        return self._order_workflow(intent, customer_id, memory, trace, state)

    # ------------------------------------------------------------------
    def _order_workflow(self, intent: IntentResult, customer_id: str, memory: SessionMemory,
                        trace: Trace, state: dict) -> AgentResponse:
        order_id = intent.order_id
        assert order_id is not None

        # 5a. lookup (first call verifies existence + ownership)
        with trace.span("lookup_agent") as attrs:
            access = self.lookup_agent.verify_access(order_id, customer_id, trace)
            attrs["access"] = access.error or "ok"
            if not access.ok:
                outcome = "access_denied" if access.error == "ACCESS_DENIED" else "order_not_found"
                if access.error == "ACCESS_DENIED":
                    trace.event("security_access_denied", order_id=order_id)
                    state["flags"] = ["ownership_check_failed"]
                # same reply for both, so the bot never confirms another customer's order exists
                return self._finish(trace, memory, state, outcome, FIXED_REPLIES["order_not_found"],
                                    intent=intent, remember_order=False)
            facts = self.lookup_agent.run(intent.intent, order_id, customer_id, trace, order=access.data)
            attrs.update(sources=facts.sources, errors=facts.errors, gathered_by=facts.gathered_by)

        # 5b. an earlier human-approval request on this order changes what we do now
        kind = {"refund": "refund", "insurance": "claim"}.get(intent.intent)
        if kind:
            with trace.span("approval_check") as attrs:
                open_items = [a for a in self.c.approvals.for_order(order_id)
                              if a["kind"] == kind and a["status"] in (PENDING, APPROVED, REJECTED)]
                attrs["found"] = [a["approval_id"] for a in open_items]
            if open_items:
                item = open_items[-1]
                if item["status"] == APPROVED:
                    with trace.span("execution_agent", mode="execute_approved"):
                        execution = self.execution_agent.execute_approved(item["approval_id"], trace)
                    facts = self.lookup_agent.run(intent.intent, order_id, customer_id, trace, order=access.data)
                    decision = PolicyDecision(kind=kind, eligible=True, decision="approved_by_human",
                                              amount=item["amount"], rule_ids=item["rule_ids"],
                                              explanation=f"Approved by {item['decided_by']}.")
                    return self._respond(trace, memory, state, intent, execution.outcome, facts, decision,
                                         execution, item)
                outcome = "approval_pending" if item["status"] == PENDING else "approval_rejected"
                return self._respond(trace, memory, state, intent, outcome, facts, None, None, item)

        # 5c. policy
        with trace.span("policy_agent") as attrs:
            decision = self.policy_agent.run(intent.intent, order_id, customer_id, facts, trace)
            attrs.update(decision=decision.decision, eligible=decision.eligible, amount=decision.amount,
                         requires_approval=decision.requires_approval, rule_ids=decision.rule_ids)

        # 5d. execution
        with trace.span("execution_agent") as attrs:
            execution = self.execution_agent.run(decision, order_id, customer_id, intent.incident, trace)
            attrs.update(outcome=execution.outcome, actions=execution.actions, approval_id=execution.approval_id)

        if execution.outcome == "action_failed":
            return self._finish(trace, memory, state, "action_failed", FIXED_REPLIES["action_failed"], intent=intent)
        approval = self.c.approvals.get(execution.approval_id) if execution.approval_id else None
        return self._respond(trace, memory, state, intent, execution.outcome, facts, decision, execution, approval)

    # ------------------------------------------------------------------
    def _respond(self, trace: Trace, memory: SessionMemory, state: dict, intent: IntentResult, outcome: str,
                 facts: Facts, decision: PolicyDecision | None, execution: ExecutionResult | None,
                 approval: dict | None) -> AgentResponse:
        with trace.span("response_agent") as attrs:
            sheet = self.response_agent.fact_sheet(outcome=outcome, order_id=intent.order_id, facts=facts,
                                                   decision=decision, execution=execution, approval=approval)
            owned = {o for o, row in self.c.backend.orders.items() if row["customer_id"] == memory.customer_id}
            reply, citations, drafted_by = self.response_agent.run(sheet, owned, trace)
            attrs.update(drafted_by=drafted_by, citations=citations)
        return self._finish(trace, memory, state, outcome, reply, intent=intent, citations=citations,
                            execution=execution, approval=approval)

    def _finish(self, trace: Trace, memory: SessionMemory | None, state: dict, outcome: str, reply: str, *,
                intent: IntentResult | None = None, citations: list[str] | None = None,
                execution: ExecutionResult | None = None, approval: dict | None = None,
                remember_order: bool = True) -> AgentResponse:
        approval_id = (execution.approval_id if execution else None) or (approval or {}).get("approval_id")
        if memory is not None:
            memory.add_turn("assistant", reply, outcome=outcome, trace_id=trace.trace_id)
            if intent:
                memory.set_fact("last_intent", intent.intent)
                if intent.order_id and remember_order:
                    memory.remember_order(intent.order_id)
                    if intent.incident:
                        memory.set_fact("incident", intent.incident)
            memory.set_fact("last_outcome", outcome)
            if approval_id and outcome in {"approval_required", "claim_approval_required"}:
                if approval_id not in memory.facts["pending_approvals"]:
                    memory.facts["pending_approvals"].append(approval_id)
            self.c.sessions.save(memory)

        actions = execution.actions if execution else []
        record = trace.finish(intent=state["intent"], route=state["route"], order_id=state["order_id"],
                              outcome=outcome, actions=actions, approval_id=approval_id,
                              guardrail_flags=state["flags"], reply=reply)
        return AgentResponse(
            trace_id=trace.trace_id, reply=reply, intent=state["intent"], route=state["route"], outcome=outcome,
            order_id=state["order_id"], actions=actions, citations=citations or [], approval_id=approval_id,
            latency_ms=record["latency_ms"], cost_usd=record["cost_usd"], llm_calls=len(trace.llm_calls),
            guardrail_flags=state["flags"],
        )

    # ------------------------------------------------------------------
    def approve(self, approval_id: str, approver: str, approve: bool, note: str = "") -> ExecutionResult:
        """Human decision entry point (used by `python main.py approvals approve|reject`)."""
        item = self.c.approvals.decide(approval_id, approver=approver, approve=approve, note=note)
        trace = self.c.tracer.start("approvals-console", item["customer_id"], f"{'approve' if approve else 'reject'} {approval_id}")
        with trace.span("human_approval", approval_id=approval_id, approver=approver, approved=approve):
            pass
        if not approve:
            result = ExecutionResult(outcome="approval_rejected", approval_id=approval_id)
        else:
            with trace.span("execution_agent", mode="execute_approved"):
                result = self.execution_agent.execute_approved(approval_id, trace)
        trace.finish(intent=item["kind"], route="human_approval", order_id=item["order_id"], outcome=result.outcome,
                     actions=result.actions, approval_id=approval_id, guardrail_flags=[], reply="")
        return result
