"""Per-request tracing with structured JSON logs.

Every customer message produces exactly one trace record in ops/logs/traces.jsonl
containing: the trace id, intent, route, every agent step (span), every tool call,
every model call (tokens, cost, latency), policy decisions, the final outcome,
and total latency/cost. The format is documented in ops/observability.md and maps
one-to-one onto OpenTelemetry spans if the customer later adopts an OTel backend.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from security.pii import redact_pii


class Trace:
    def __init__(self, tracer: "Tracer", session_id: str, customer_id: str, message: str):
        self._tracer = tracer
        self._listener = tracer.listener
        self.trace_id = "tr-" + uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.customer_id = customer_id
        self.message = redact_pii(message[:500])
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._t0 = time.perf_counter()
        self.spans: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []

    def _offset_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 2)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        """Time one agent step. Attributes added to the yielded dict are recorded."""
        record: dict[str, Any] = {"name": name, "start_ms": self._offset_ms(), "status": "ok", "attrs": dict(attrs)}
        self._emit({"type": "span_start", "name": name, "at_ms": record["start_ms"]})
        start = time.perf_counter()
        try:
            yield record["attrs"]
        except Exception as exc:  # recorded, then re-raised for the orchestrator to handle
            record["status"] = "error"
            record["attrs"]["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
            self.spans.append(record)
            self._emit({"type": "span_end", **record})

    def _emit(self, event: dict[str, Any]) -> None:
        """Push a live event to a listener (the web UI streams these as they happen)."""
        if self._listener is not None:
            try:
                self._listener(event)
            except Exception:
                pass  # observability must never break the request

    def event(self, name: str, **attrs: Any) -> None:
        item = {"name": name, "at_ms": self._offset_ms(), **attrs}
        self.events.append(item)
        self._emit({"type": "event", **item})

    def record_tool(self, **fields: Any) -> None:
        item = {"at_ms": self._offset_ms(), **fields}
        self.tool_calls.append(item)
        self._emit({"type": "tool", **item})

    def record_llm(self, **fields: Any) -> None:
        item = {"at_ms": self._offset_ms(), **fields}
        self.llm_calls.append(item)
        self._emit({"type": "llm", **item})

    @property
    def cost_usd(self) -> float:
        return round(sum(c.get("cost_usd", 0.0) for c in self.llm_calls), 6)

    @property
    def elapsed_ms(self) -> float:
        return self._offset_ms()

    def finish(self, **result: Any) -> dict[str, Any]:
        record = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "customer_id": self.customer_id,
            "started_at": self.started_at,
            "message": self.message,
            "latency_ms": self.elapsed_ms,
            "cost_usd": self.cost_usd,
            "llm_tokens": {
                "input": sum(c.get("input_tokens", 0) for c in self.llm_calls),
                "output": sum(c.get("output_tokens", 0) for c in self.llm_calls),
            },
            **result,
            "spans": self.spans,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "events": self.events,
        }
        self._tracer.write(record)
        return record


_WRITE_LOCK = threading.Lock()


class Tracer:
    """Creates traces and writes finished ones as one JSON line each."""

    def __init__(self, log_dir: Path | None, echo: bool = False):
        self.log_dir = log_dir
        self.echo = echo
        self.last_record: dict[str, Any] | None = None  # read by the web UI's trace panel
        self.listener: Any = None  # optional callable(event) for live streaming
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

    def start(self, session_id: str, customer_id: str, message: str) -> Trace:
        return Trace(self, session_id, customer_id, message)

    def write(self, record: dict[str, Any]) -> None:
        self.last_record = record
        line = json.dumps(record, default=str)
        if self.log_dir:
            with _WRITE_LOCK, (self.log_dir / "traces.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        if self.echo:
            summary = {k: record.get(k) for k in ("trace_id", "intent", "route", "outcome", "latency_ms", "cost_usd")}
            print("[trace] " + json.dumps(summary), file=sys.stderr)
