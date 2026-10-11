"""Per-visitor sandboxes for the public web demo.

Every browser gets its own isolated copy of the simulated backends, approval
queue, and session memory, so visitors never see each other's refunds, and
"Reset demo" is just throwing the sandbox away. Policy settings (approval limit,
refund window) are per sandbox and can be hot-reloaded without a restart.
"""
from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections import deque
from typing import Any

from agents.llm import LLMClient
from agents.orchestrator import Components, Orchestrator
from config import Settings
from memory.session import SessionStore
from ops.observability import Tracer
from security.approvals import ApprovalQueue
from tools.audit import AuditLog
from tools.backend import Backend

VISITOR_ID = re.compile(r"^[a-f0-9]{8,32}$")


class Sandbox:
    def __init__(self, visitor_id: str, base: Backend, settings: Settings, log_dir):
        self.visitor_id = visitor_id
        self.lock = threading.Lock()
        self.created = time.time()
        self.last_used = time.time()
        self.message_times: deque[float] = deque()
        self.backend = base.fresh_copy()
        self.tracer = Tracer(log_dir, echo=True)
        self.audit = AuditLog(None)
        self.approvals = ApprovalQueue(None)
        self.sessions = SessionStore(None)
        self.settings = settings
        self.orchestrator = self._build(settings)

    def _build(self, settings: Settings) -> Orchestrator:
        llm = LLMClient(dataclasses.replace(settings, llm_mode="offline"))
        components = Components(settings=settings, backend=self.backend, tracer=self.tracer, audit=self.audit,
                                approvals=self.approvals, sessions=self.sessions, llm=llm)
        return Orchestrator(components)

    def apply_policy(self, *, approval_threshold: float, refund_window_days: int) -> None:
        """Hot-reload business rules: rebuild the agents with new settings, keep all state."""
        self.settings = dataclasses.replace(self.settings, refund_approval_threshold=approval_threshold,
                                            refund_window_days=refund_window_days)
        self.orchestrator = self._build(self.settings)

    def use_model(self, claude_client: Any | None) -> None:
        llm = self.orchestrator.c.llm
        if claude_client is not None:
            llm.mode, llm._client = "claude", claude_client
        else:
            llm.mode, llm._client = "offline", None

    def allow_message(self, limit: int, window_s: int) -> bool:
        now = time.time()
        while self.message_times and now - self.message_times[0] > window_s:
            self.message_times.popleft()
        if len(self.message_times) >= limit:
            return False
        self.message_times.append(now)
        return True


class SandboxManager:
    def __init__(self, settings: Settings, max_sandboxes: int = 300, ttl_s: int = 2 * 3600):
        self.settings = settings
        self.base = Backend.from_dir(settings.data_dir)
        self.max = max_sandboxes
        self.ttl = ttl_s
        self._items: dict[str, Sandbox] = {}
        self._lock = threading.Lock()

    def get(self, visitor_id: str) -> Sandbox:
        with self._lock:
            self._evict()
            box = self._items.get(visitor_id)
            if box is None:
                box = Sandbox(visitor_id, self.base, self.settings, self.settings.log_dir)
                self._items[visitor_id] = box
            box.last_used = time.time()
            return box

    def reset(self, visitor_id: str) -> None:
        with self._lock:
            self._items.pop(visitor_id, None)

    def _evict(self) -> None:
        now = time.time()
        for vid in [v for v, b in self._items.items() if now - b.last_used > self.ttl]:
            del self._items[vid]
        while len(self._items) >= self.max:
            oldest = min(self._items.values(), key=lambda b: b.last_used)
            del self._items[oldest.visitor_id]


class DailyCap:
    """Global cap on Claude-backed requests per UTC day, protecting the API budget."""

    def __init__(self, limit: int):
        self.limit = limit
        self._day = ""
        self._count = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self.limit:
                return False
            self._count += 1
            return True

    @property
    def remaining(self) -> int:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        return self.limit if today != self._day else max(0, self.limit - self._count)


class CircuitBreaker:
    """Stops calling Claude for a cool-down period after repeated failures.

    closed    -> normal; Claude is used.
    open      -> after `threshold` failures within `window_s`; Claude is skipped for
                 `cooldown_s` and every reply is served instantly in rules-only mode.
    half-open -> after the cool-down, the next request tries Claude again; success
                 closes the breaker, failure opens it again.
    """

    def __init__(self, threshold: int = 3, window_s: int = 120, cooldown_s: int = 300):
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            return time.time() - self._opened_at >= self.cooldown_s  # half-open trial

    def record(self, ok: bool) -> None:
        with self._lock:
            now = time.time()
            if ok:
                self._failures.clear()
                self._opened_at = None
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window_s:
                self._failures.popleft()
            if len(self._failures) >= self.threshold or self._opened_at is not None:
                self._opened_at = now

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if time.time() - self._opened_at >= self.cooldown_s else "open"
