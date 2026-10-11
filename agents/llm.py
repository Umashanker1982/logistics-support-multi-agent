"""Thin wrapper around the Anthropic Python SDK.

Responsibilities:
* choose offline vs Claude mode from settings (offline if no API key);
* apply timeout and retry settings;
* record tokens, latency, and cost for every call in the request trace;
* never raise: on any API failure it returns None and the calling agent falls
  back to its deterministic rules, so a model outage degrades quality, not uptime.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from config import Settings
from ops.observability import Trace


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mode = settings.llm_mode
        self.warning: str | None = None
        self._client: Any = None
        if self.mode == "claude":
            if not settings.anthropic_api_key:
                self.mode = "offline"
                self.warning = "LLM_MODE=claude but ANTHROPIC_API_KEY is not set; running offline."
            else:
                try:
                    import anthropic
                except ImportError:
                    self.mode = "offline"
                    self.warning = "The 'anthropic' package is not installed; running offline."
                else:
                    self._client = anthropic.Anthropic(
                        api_key=settings.anthropic_api_key,
                        timeout=settings.llm_timeout_s,
                        max_retries=settings.llm_max_retries,
                    )

    @property
    def enabled(self) -> bool:
        return self.mode == "claude" and self._client is not None

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        s = self.settings
        return (input_tokens * s.price_input_per_mtok + output_tokens * s.price_output_per_mtok) / 1_000_000

    def create(
        self,
        *,
        trace: Trace,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 500,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any | None:
        """Call Claude once. Returns the SDK Message, or None on any failure."""
        if not self.enabled:
            return None
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        t0 = time.perf_counter()
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            trace.record_llm(agent=agent, model=self.settings.model, ok=False,
                             error=type(exc).__name__, latency_ms=round((time.perf_counter() - t0) * 1000, 2))
            trace.event("llm_fallback", agent=agent, error=type(exc).__name__)
            return None
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        trace.record_llm(agent=agent, model=self.settings.model, ok=True,
                         input_tokens=in_tok, output_tokens=out_tok,
                         cost_usd=round(self.cost(in_tok, out_tok), 6),
                         stop_reason=getattr(resp, "stop_reason", None),
                         latency_ms=round((time.perf_counter() - t0) * 1000, 2))
        return resp

    @staticmethod
    def text_of(resp: Any) -> str:
        return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()

    @staticmethod
    def json_of(resp: Any) -> dict[str, Any] | None:
        """Extract the first JSON object from a text response."""
        text = LLMClient.text_of(resp)
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
