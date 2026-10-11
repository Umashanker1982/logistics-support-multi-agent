"""General agent: answers non-order questions (hours, contact, how to return) from the FAQ only."""
from __future__ import annotations

from agents.llm import LLMClient
from agents.prompts import GENERAL_SYSTEM
from agents.types import IntentResult
from ops.observability import Trace
from security.guardrails import validate_output
from tools.backend import Backend

DEFAULT_REPLY = ("I can help with refunds, parcel tracking, and shipping-insurance claims. "
                 "Tell me your order number (it starts with ORD- followed by four digits) and what happened, "
                 "or reply 'agent' to reach a person.")


class GeneralAgent:
    name = "general_agent"

    def __init__(self, llm: LLMClient, backend: Backend):
        self.llm = llm
        self.backend = backend

    def run(self, text: str, intent: IntentResult, trace: Trace) -> tuple[str, str]:
        """Returns (reply, outcome)."""
        faq = self.backend.faq
        if self.llm.enabled:
            faq_block = "\n".join(f"- {k}: {v}" for k, v in faq.items())
            resp = self.llm.create(
                trace=trace, agent=self.name, system=GENERAL_SYSTEM, max_tokens=200,
                messages=[{"role": "user",
                           "content": f"<faq>\n{faq_block}\n</faq>\n<customer_message>\n{text}\n</customer_message>"}])
            if resp is not None:
                draft = LLMClient.text_of(resp)
                check = validate_output(draft, allowed_sources=set(), owned_order_ids=set(), money_moved=False,
                                        max_chars=800)
                if check.ok:
                    return draft, "general_answer"
                trace.event("response_rejected_by_output_guardrail", problems=check.problems)
        if intent.faq_topic in faq:
            return faq[intent.faq_topic], "general_answer"
        return DEFAULT_REPLY, "needs_clarification"
