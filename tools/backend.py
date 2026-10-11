"""Simulated order, shipping, refund, claims, and ticketing backends.

The seed data lives in tools/data/backend_seed.json. Writes (refunds, claims,
tickets) are applied in memory and, if a ledger path is given, appended to a
JSON-lines ledger that is replayed on start-up. That gives the interactive CLI
persistent state across runs (so a refund approved in one process is visible in
the next) while the benchmark uses a fresh, isolated in-memory copy per case.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import date
from pathlib import Path
from typing import Any


class Backend:
    def __init__(self, seed: dict[str, Any], policies: dict[str, Any], ledger_path: Path | None = None):
        self._seed = seed
        self.as_of: date = date.fromisoformat(seed["as_of"])
        self.customers: dict[str, dict] = copy.deepcopy(seed["customers"])
        self.orders: dict[str, dict] = copy.deepcopy(seed["orders"])
        self.shipments: dict[str, dict] = copy.deepcopy(seed["shipments"])
        self.returns: dict[str, dict] = copy.deepcopy(seed["returns"])
        self.refunds: dict[str, dict] = copy.deepcopy(seed["refunds"])
        self.claims: dict[str, dict] = copy.deepcopy(seed["claims"])
        self.tickets: dict[str, dict] = copy.deepcopy(seed["tickets"])
        self.rules: dict[str, str] = policies["rules"]
        self.faq: dict[str, str] = policies["faq"]
        self._ledger_path = ledger_path
        self._lock = threading.Lock()
        self._counters = {"refund": 6000, "claim": 7000, "ticket": 8000}
        if ledger_path and ledger_path.exists():
            self._replay(ledger_path)

    # ---------- construction ----------
    @classmethod
    def from_dir(cls, data_dir: Path, ledger_path: Path | None = None) -> "Backend":
        seed = json.loads((data_dir / "backend_seed.json").read_text(encoding="utf-8"))
        policies = json.loads((data_dir / "policies.json").read_text(encoding="utf-8"))
        return cls(seed, policies, ledger_path)

    def fresh_copy(self) -> "Backend":
        """An isolated copy with no ledger, used by the benchmark and tests."""
        return Backend(self._seed, {"rules": self.rules, "faq": self.faq}, ledger_path=None)

    # ---------- writes ----------
    def next_id(self, kind: str, prefix: str) -> str:
        with self._lock:
            self._counters[kind] += 1
            return f"{prefix}-{self._counters[kind]}"

    def record(self, table: str, key: str, row: dict[str, Any]) -> None:
        """Insert a row into a writable table and append it to the ledger."""
        if table not in {"refunds", "claims", "tickets"}:
            raise ValueError(f"table {table!r} is read-only")
        with self._lock:
            getattr(self, table)[key] = row
            if self._ledger_path:
                self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self._ledger_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"table": table, "key": key, "row": row}) + "\n")

    def _replay(self, path: Path) -> None:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            getattr(self, event["table"])[event["key"]] = event["row"]
            # keep id counters ahead of anything already issued
            for kind, field in (("refund", "refund_id"), ("claim", "claim_id"), ("ticket", "ticket_id")):
                ident = event["row"].get(field)
                if ident and ident.split("-")[-1].isdigit():
                    self._counters[kind] = max(self._counters[kind], int(ident.split("-")[-1]))
