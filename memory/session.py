"""Session conversation history plus structured working memory, persisted to disk.

* history -- the ordered list of user/assistant turns (last N are given to the models).
* facts   -- a small structured "working memory" that agents read and update:

    current_order_id     the order the conversation is about
    last_intent          intent of the previous turn
    incident             what the customer said happened (damaged, lost, ...)
    last_outcome         outcome of the previous turn
    pending_approvals    approval ids waiting on a human
    orders_discussed     every order id mentioned in this session

Example of earlier context changing a later decision:
  turn 1: "Where is ORD-1007?"                        -> facts.current_order_id = ORD-1007
  turn 2: "It arrived smashed, can I claim insurance?" -> no order id in the message, so the
          intent agent fills ORD-1007 from memory and the claim is filed for the right order.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_HISTORY = 50


@dataclass
class SessionMemory:
    session_id: str
    customer_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=lambda: {
        "current_order_id": None,
        "last_intent": None,
        "incident": None,
        "last_outcome": None,
        "pending_approvals": [],
        "orders_discussed": [],
    })

    def add_turn(self, role: str, content: str, **meta: Any) -> None:
        self.history.append({"role": role, "content": content,
                             "ts": datetime.now(timezone.utc).isoformat(), **meta})
        self.history = self.history[-MAX_HISTORY:]

    def set_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)

    def remember_order(self, order_id: str) -> None:
        self.facts["current_order_id"] = order_id
        if order_id not in self.facts["orders_discussed"]:
            self.facts["orders_discussed"].append(order_id)

    def recent_history(self, n: int = 6) -> list[dict[str, str]]:
        return [{"role": t["role"], "content": t["content"]} for t in self.history[-n:]]

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "customer_id": self.customer_id,
                "history": self.history, "facts": self.facts}


class SessionStore:
    """Loads and saves sessions as JSON files (one per session).

    A saved session is bound to the customer who created it: loading it with a
    different customer id is refused, so one customer cannot resume another's
    conversation by guessing a session id.
    """

    def __init__(self, directory: Path | None):
        self.directory = directory
        self._cache: dict[str, SessionMemory] = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise ValueError("session_id may only contain letters, digits, '-' and '_'")
        assert self.directory is not None
        return self.directory / f"{session_id}.json"

    def get(self, session_id: str, customer_id: str) -> SessionMemory:
        mem = self._cache.get(session_id)
        if mem is None and self.directory and self._path(session_id).exists():
            raw = json.loads(self._path(session_id).read_text(encoding="utf-8"))
            mem = SessionMemory(raw["session_id"], raw["customer_id"], raw["history"], raw["facts"])
        if mem is None:
            mem = SessionMemory(session_id, customer_id)
        if mem.customer_id != customer_id:
            raise PermissionError("This session belongs to a different customer.")
        self._cache[session_id] = mem
        return mem

    def save(self, mem: SessionMemory) -> None:
        self._cache[mem.session_id] = mem
        if self.directory:
            self._path(mem.session_id).write_text(json.dumps(mem.to_dict(), indent=2), encoding="utf-8")
