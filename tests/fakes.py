"""A fake Anthropic client so Claude-mode code paths are tested without an API key or network."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any, Callable

from agents.prompts import GENERAL_SYSTEM, INTENT_SYSTEM, LOOKUP_SYSTEM, RESPONSE_SYSTEM


def text_msg(text: str, in_tok: int = 300, out_tok: int = 60) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok))


def tool_msg(calls: list[tuple[str, dict[str, Any]]]) -> SimpleNamespace:
    blocks = [SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args)
              for i, (name, args) in enumerate(calls)]
    return SimpleNamespace(content=blocks, stop_reason="tool_use",
                           usage=SimpleNamespace(input_tokens=400, output_tokens=40))


def default_handler(kwargs: dict[str, Any]) -> SimpleNamespace:
    """Behaves like a well-mannered model for each agent prompt."""
    system, messages = kwargs["system"], kwargs["messages"]
    first = messages[0]["content"]
    if system == INTENT_SYSTEM:
        ids = re.findall(r"ORD-\d{4}", first)
        low = first.lower()
        intent = ("insurance" if "claim" in low or "insurance" in low else
                  "refund" if "refund" in low or "money back" in low else
                  "track" if ids or "where" in low else "general")
        return text_msg(json.dumps({"intent": intent, "order_id": ids[0] if ids else None,
                                    "incident": "damaged" if "smash" in low else None,
                                    "faq_topic": "hours" if "hours" in low else None, "confidence": 0.95}))
    if system == LOOKUP_SYSTEM:
        if len(messages) == 1:  # first round: ask for the order and its return/shipment
            order_id = re.search(r"ORD-\d{4}", first).group(0)
            return tool_msg([("get_order", {"order_id": order_id}), ("get_return_status", {"order_id": order_id})])
        return text_msg("DONE")
    if system == RESPONSE_SYSTEM:
        sheet = json.loads(first)
        cite = sheet["allowed_citations"][0]
        return text_msg(f"Here is an update on your request [{cite}]. Outcome: {sheet['outcome'].replace('_', ' ')}.")
    if system == GENERAL_SYSTEM:
        return text_msg("Support is open Monday to Friday, 8am to 8pm Eastern.")
    raise AssertionError("unexpected system prompt")


class FakeAnthropic:
    def __init__(self, handler: Callable[[dict[str, Any]], Any] = default_handler):
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self._handler = handler

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._handler(kwargs)
