"""Response agent: drafts the customer-facing reply from verified facts, with citations.

Claude mode: Claude Haiku writes the reply from a JSON fact sheet. The draft is
then checked by the output guardrail (security/guardrails.validate_output). If it
cites anything not in the fact sheet, mentions another order, or claims money
moved when it did not, the draft is discarded and the deterministic template is
used instead. Offline mode uses the templates directly.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.llm import LLMClient
from agents.prompts import RESPONSE_SYSTEM
from agents.types import ExecutionResult, Facts, PolicyDecision
from ops.observability import Trace
from security.guardrails import _CITATION_RE, validate_output
from tools.backend import Backend

MONEY_OUTCOMES = {"refund_issued", "claim_filed", "refund_already_issued", "claim_already_filed"}

SAFE_FALLBACK = ("I'm sorry, I couldn't complete that automatically. I've noted your request and a member "
                 "of our support team will follow up.")


_RECORD_ID = re.compile(r"(?<!\[)\b((?:ORD|RF|CLM|APR|TCK)-\d{4})\b(?!\])")


def bracket_verified_ids(text: str, allowed: set[str]) -> str:
    """Turn plain record ids that are in the verified fact sheet into citations: ORD-1002 -> [ORD-1002].

    Only ids already in `allowed` are touched, so this can never create a citation
    to something the model invented. Unknown ids are left for the output guardrail.
    """
    return _RECORD_ID.sub(lambda m: f"[{m.group(1)}]" if m.group(1) in allowed else m.group(1), text)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _clause(text: str) -> str:
    """Lower-case the first letter so a policy explanation reads naturally after a colon."""
    return text[:1].lower() + text[1:] if text else text


class ResponseAgent:
    name = "response_agent"

    def __init__(self, llm: LLMClient, backend: Backend, approval_threshold: float = 200.0):
        self.llm = llm
        self.backend = backend
        self.approval_threshold = approval_threshold

    # ---------- fact sheet ----------
    def fact_sheet(self, *, outcome: str, order_id: str | None, facts: Facts | None,
                   decision: PolicyDecision | None, execution: ExecutionResult | None,
                   approval: dict[str, Any] | None) -> dict[str, Any]:
        sources: list[str] = []
        if order_id and facts and facts.order:
            sources.append(order_id)
        if facts:
            sources += [s for s in facts.sources if s not in sources]
        if decision:
            sources += [f"POLICY {r}" for r in decision.rule_ids]
        if execution:
            sources += [a["ref"] for a in execution.actions if a.get("type") != "error"]
        if approval:
            sources.append(approval["approval_id"])
            if approval.get("ticket_id"):
                sources.append(approval["ticket_id"])
        sources = list(dict.fromkeys(s for s in sources if s))
        order = facts.order if facts else None
        return {
            "outcome": outcome,
            "order": None if not order else {
                "order_id": order_id, "status": order["status"], "delivered_on": order["delivered_on"],
                "items": [i["name"] for i in order["items"]], "total": order["total"]},
            "shipment": facts.shipment if facts else None,
            "existing_refund": facts.refund if facts and facts.refund and facts.refund.get("refunded") else None,
            "existing_claim": facts.claim if facts and facts.claim and facts.claim.get("claimed") else None,
            "decision": None if not decision else {
                "decision": decision.decision, "amount": decision.amount, "explanation": decision.explanation,
                "rules": {r: self.backend.rules.get(r, "") for r in decision.rule_ids}},
            "actions": execution.actions if execution else [],
            "approval": approval,
            "refund_timing": self.backend.faq["refund_time"],
            "approval_limit": self.approval_threshold,
            "allowed_citations": sources,
        }

    # ---------- templates (offline mode and fallback) ----------
    def template(self, sheet: dict[str, Any]) -> str:
        o = sheet["outcome"]
        order = sheet["order"] or {}
        oid = order.get("order_id")
        item = ", ".join(order.get("items", [])) or "your item"
        d = sheet["decision"] or {}
        rule = next(iter(d.get("rules", {})), None)
        rule_cite = f" [POLICY {rule}]" if rule else ""
        refs = {a["type"]: a["ref"] for a in sheet["actions"]}
        appr = sheet["approval"] or {}
        ship = sheet["shipment"] or {}

        if o == "refund_issued":
            return (f"Good news: your refund of {_money(d.get('amount', order.get('total', 0)))} for the {item} [{oid}] "
                    f"has been issued (refund [{refs['refund_issued']}]). "
                    f"{sheet['refund_timing']}")
        if o == "approval_required":
            return (f"Your return of the {item} [{oid}] qualifies for a {_money(d['amount'])} refund. Because it is "
                    f"above our {_money(sheet['approval_limit'])} approval limit, a support lead must review it before payment [POLICY R-4]. "
                    f"Your approval reference is [{appr['approval_id']}], and we'll update you "
                    f"as soon as it's reviewed.")
        if o == "claim_approval_required":
            return (f"Your insurance claim for the {item} [{oid}] is eligible for a {_money(d['amount'])} payout. "
                    f"Payouts above {_money(sheet['approval_limit'])} need a claims lead to review them first [POLICY I-4]. "
                    f"Your approval reference is [{appr['approval_id']}].")
        if o == "approval_pending":
            return (f"Your {appr.get('kind', 'request')} for {_money(appr.get('amount', 0))} on [{oid}] is still "
                    f"waiting for review by a support lead (reference "
                    f"[{appr['approval_id']}]). We'll let you know as soon as there's a decision.")
        if o == "approval_rejected":
            return (f"A support lead reviewed your {appr.get('kind', 'request')} for [{oid}] (reference "
                    f"[{appr['approval_id']}]) and wasn't able to approve it. Reply 'agent' and a team member "
                    f"will explain the decision.")
        if o == "refund_denied":
            return (f"I checked your order [{oid}] and can't issue a cash refund right now: "
                    f"{_clause(d.get('explanation', ''))}{rule_cite} If you think this is wrong, reply 'agent' and "
                    f"a team member will take a look.")
        if o == "refund_already_issued":
            r = sheet["existing_refund"] or {}
            return (f"A refund of {_money(r.get('amount', 0))} for [{oid}] was already issued on "
                    f"{r.get('issued_on')} (refund [{r.get('refund_id')}]). "
                    f"{sheet['refund_timing']}")
        if o == "claim_filed":
            return (f"I've filed your insurance claim for the {item} [{oid}] (claim "
                    f"[{refs['claim_filed']}]), with a payout of {_money(d['amount'])} [POLICY I-2].")
        if o == "claim_denied":
            return (f"I checked the insurance on your order [{oid}], but a claim can't be filed: "
                    f"{_clause(d.get('explanation', ''))}{rule_cite}")
        if o == "claim_already_filed":
            c = sheet["existing_claim"] or {}
            return (f"There's already an insurance claim on your order [{oid}]: claim "
                    f"[{c.get('claim_id')}], status {c.get('status', 'open').replace('_', ' ')}.")
        if o == "tracking_info":
            if not ship:
                return f"Your order [{oid}] is currently marked as {order.get('status', 'processing').replace('_', ' ')}."
            if ship.get("status") == "delivered":
                return (f"Your order [{oid}] was delivered by {ship['carrier']} on {order.get('delivered_on')} "
                        f"(last scan: {ship['last_scan']}).")
            return (f"Your order [{oid}] is in transit with {ship['carrier']} (tracking {ship['tracking_no']}). "
                    f"Last scan: {ship['last_scan']}. Expected delivery: {ship.get('eta') or 'to be confirmed'}.")
        if o == "tracking_exception":
            return (f"{ship.get('carrier', 'The carrier')} reported a problem with your order [{oid}]: "
                    f"{ship.get('last_scan', 'delivery exception')}. I've opened investigation ticket "
                    f"[{refs.get('ticket_created')}] [POLICY T-1], and we'll update "
                    f"you within 2 business days.")
        return SAFE_FALLBACK

    # ---------- required citations ----------
    @staticmethod
    def required_citations(sheet: dict[str, Any]) -> list[str]:
        """The sources every reply with this outcome must show, taken only from verified facts."""
        allowed = set(sheet["allowed_citations"])
        o = sheet["outcome"]
        order = (sheet.get("order") or {}).get("order_id")
        rules = list((sheet.get("decision") or {}).get("rules", {}))
        refs = {a["type"]: a["ref"] for a in sheet.get("actions", [])}
        appr = (sheet.get("approval") or {}).get("approval_id")
        need: list[str] = [order] if order else []
        if o in {"approval_required", "claim_approval_required"}:
            need += [f"POLICY {r}" for r in rules if r in {"R-4", "I-4"}] + [appr]
        elif o in {"refund_denied", "claim_denied"} and rules:
            need.append(f"POLICY {rules[0]}")
        elif o == "refund_issued":
            need.append(refs.get("refund_issued"))
        elif o == "claim_filed":
            need.append(refs.get("claim_filed"))
        elif o == "tracking_exception":
            need += ["POLICY T-1", refs.get("ticket_created")]
        elif o in {"approval_pending", "approval_rejected"}:
            need.append(appr)
        return [c for c in dict.fromkeys(need) if c and c in allowed]

    @classmethod
    def ensure_citations(cls, reply: str, sheet: dict[str, Any]) -> str:
        """Append a short Sources line for any required citation the draft left out."""
        present = set(_CITATION_RE.findall(reply))
        missing = [c for c in cls.required_citations(sheet) if c not in present]
        return reply if not missing else reply.rstrip() + "\n\nSources: " + " ".join(f"[{c}]" for c in missing)

    # ---------- main entry ----------
    def run(self, sheet: dict[str, Any], owned_order_ids: set[str], trace: Trace) -> tuple[str, list[str], str]:
        """Returns (reply, citations, drafted_by)."""
        allowed = set(sheet["allowed_citations"])
        money_moved = sheet["outcome"] in MONEY_OUTCOMES
        if self.llm.enabled:
            resp = self.llm.create(trace=trace, agent=self.name, system=RESPONSE_SYSTEM, max_tokens=350,
                                   messages=[{"role": "user", "content": json.dumps(sheet, default=str)}])
            if resp is not None:
                draft = self.ensure_citations(bracket_verified_ids(LLMClient.text_of(resp), allowed), sheet)
                check = validate_output(draft, allowed_sources=allowed, owned_order_ids=owned_order_ids,
                                        money_moved=money_moved)
                cites = _CITATION_RE.findall(draft)
                if check.ok and cites:
                    return draft, list(dict.fromkeys(cites)), "claude"
                trace.event("response_rejected_by_output_guardrail", problems=check.problems or ["no_citation"])
        reply = self.ensure_citations(self.template(sheet), sheet)
        check = validate_output(reply, allowed_sources=allowed, owned_order_ids=owned_order_ids,
                                money_moved=money_moved)
        if not check.ok:  # should never happen; tests assert templates always pass
            trace.event("template_failed_output_guardrail", problems=check.problems)
            return SAFE_FALLBACK, [], "fallback"
        return reply, list(dict.fromkeys(_CITATION_RE.findall(reply))), "template"
