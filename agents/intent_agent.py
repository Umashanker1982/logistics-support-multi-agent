"""Intent agent: classifies the request and extracts entities (order id, incident, FAQ topic).

Two classifiers sit behind one interface:
* rules  -- keyword and regex classifier, deterministic and free (offline mode and fallback);
* claude -- Claude Haiku returning strict JSON, validated before use.

Whichever classifier runs, the order id is then resolved against session memory:
if the message has no order id, the order from earlier in the conversation is used.
A model-proposed order id that does not literally appear in the message is discarded
(hallucination guard), so the model can never redirect an action to another order.
"""
from __future__ import annotations

import re

from agents.llm import LLMClient
from agents.prompts import INTENT_SYSTEM
from agents.types import IntentResult
from memory.session import SessionMemory
from ops.observability import Trace
from security.guardrails import ORDER_ID_RE, normalize_order_id

VALID_INTENTS = {"refund", "track", "insurance", "general"}
VALID_INCIDENTS = {"damaged", "lost", "defective", "change_of_mind", "wrong_item", "late"}
VALID_FAQ = {"hours", "contact", "refund_time", "return_how", "insurance_what"}

_INCIDENT_PATTERNS = [
    ("change_of_mind", r"changed my mind|change of mind|don'?t want it|no longer need|don'?t need it"),
    ("wrong_item", r"wrong item|wrong product|not what i ordered|sent me the wrong"),
    ("damaged", r"damaged|broken|smashed|cracked|shattered|dented|crushed"),
    ("lost", r"\blost\b|never arrived|never received|didn'?t arrive|missing parcel|missing package|not delivered"),
    ("defective", r"defective|faulty|doesn'?t work|does not work|not working|stopped working"),
    ("late", r"\blate\b|delayed|taking forever|still waiting"),
]

_FAQ_PATTERNS = [
    ("refund_time", r"how long.*refund|refund.*how long|when will i get my money"),
    ("return_how", r"how (do|can) i (start a |make a )?return|return label|how to return"),
    ("insurance_what", r"what does (shipping )?insurance cover|what is (shipping )?insurance"),
    ("hours", r"\bhours\b|open(ing)? times|when are you open|what time"),
    ("contact", r"\bhuman\b|real person|\bagent\b|talk to (someone|a person)|phone number|email you"),
]


def _first(patterns: list[tuple[str, str]], text: str) -> str | None:
    for label, pattern in patterns:
        if re.search(pattern, text, re.I):
            return label
    return None


def classify_with_rules(text: str) -> IntentResult:
    t = text.lower()
    ids = ORDER_ID_RE.findall(text)
    order_id = normalize_order_id(ids[0]) if ids else None
    incident = _first(_INCIDENT_PATTERNS, t)
    faq = _first(_FAQ_PATTERNS, t)

    if re.search(r"insurance|\bclaim\b|file a claim", t):
        intent, conf = "insurance", 0.9
    elif faq == "refund_time" and not order_id:
        intent, conf = "general", 0.8
    elif re.search(r"refund|money back|reimburs|returned (it|the|my|item)|sent it back|return(ed)? .* weeks? ago", t):
        intent, conf = "refund", 0.9
    elif re.search(r"where is|track|tracking|status of my (order|parcel|package)|when will .* (arrive|deliver)|"
                   r"hasn'?t arrived|not arrived|eta|shipped yet|out for delivery", t):
        intent, conf = "track", 0.85
    elif incident in {"damaged"}:
        intent, conf = "insurance", 0.6
    elif incident == "lost":
        intent, conf = "track", 0.6
    elif incident in {"defective", "wrong_item", "change_of_mind"}:
        intent, conf = "refund", 0.6
    elif faq:
        intent, conf = "general", 0.8
    elif order_id:
        # An order id with no clear request ("can you check ORD-1004?") is treated as a status lookup.
        intent, conf = "track", 0.5
    else:
        intent, conf = "general", 0.3
    return IntentResult(intent=intent, order_id=order_id, order_id_source="message" if order_id else None,
                        incident=incident, faq_topic=faq if intent == "general" else None,
                        confidence=conf, classifier="rules")


def _classify_with_claude(llm: LLMClient, trace: Trace, text: str, memory: SessionMemory) -> IntentResult | None:
    context = ""
    if memory.get_fact("current_order_id"):
        context = f"\nConversation context: the customer was previously discussing {memory.get_fact('current_order_id')}."
    resp = llm.create(
        trace=trace, agent="intent_agent", system=INTENT_SYSTEM, max_tokens=200,
        messages=[{"role": "user", "content": f"<customer_message>\n{text}\n</customer_message>{context}"}],
    )
    if resp is None:
        return None
    data = LLMClient.json_of(resp)
    if not data or data.get("intent") not in VALID_INTENTS:
        trace.event("intent_invalid_model_output", raw=LLMClient.text_of(resp)[:200])
        return None

    order_id = data.get("order_id")
    if isinstance(order_id, str) and order_id.upper() in {normalize_order_id(m) for m in ORDER_ID_RE.findall(text)}:
        order_id = order_id.upper()
    else:
        if order_id:
            trace.event("intent_discarded_unseen_order_id", proposed=str(order_id)[:20])
        order_id = None
    incident = data.get("incident") if data.get("incident") in VALID_INCIDENTS else None
    faq = data.get("faq_topic") if data.get("faq_topic") in VALID_FAQ else None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
    except (TypeError, ValueError):
        conf = 0.7
    return IntentResult(intent=data["intent"], order_id=order_id, order_id_source="message" if order_id else None,
                        incident=incident, faq_topic=faq if data["intent"] == "general" else None,
                        confidence=conf, classifier="claude")


class IntentAgent:
    name = "intent_agent"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def run(self, text: str, memory: SessionMemory, trace: Trace) -> IntentResult:
        result = None
        if self.llm.enabled:
            result = _classify_with_claude(self.llm, trace, text, memory)
        if result is None:
            result = classify_with_rules(text)

        # Memory: fill a missing order id from the conversation so far.
        if result.order_id is None and result.intent != "general" and memory.get_fact("current_order_id"):
            result.order_id = memory.get_fact("current_order_id")
            result.order_id_source = "memory"
        # Memory: keep the incident the customer described in an earlier turn.
        if result.incident is None and result.order_id and result.order_id == memory.get_fact("current_order_id"):
            result.incident = memory.get_fact("incident")
        return result
