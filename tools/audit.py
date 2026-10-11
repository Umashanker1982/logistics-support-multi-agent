"""Append-only audit log of every tool call: who called what, why, and what happened.

Each entry carries the request trace id so an auditor can join the audit log to
the full request trace in ops/logs/traces.jsonl.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, log_dir: Path | None):
        self.path = (log_dir / "audit.jsonl") if log_dir else None
        self.entries: list[dict[str, Any]] = []  # in-memory copy for tests and the benchmark
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_action(
        self,
        *,
        trace_id: str,
        customer_id: str,
        actor: str,
        tool: str,
        args: dict[str, Any],
        reason: str,
        ok: bool,
        error: str | None,
        source: str | None,
    ) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "customer_id": customer_id,
            "actor": actor,
            "tool": tool,
            "args": args,
            "reason": reason,
            "ok": ok,
            "error": error,
            "source": source,
        }
        self.entries.append(entry)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        return entry
