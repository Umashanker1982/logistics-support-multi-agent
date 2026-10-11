"""Human-in-the-loop approval queue for high-value refunds and claims.

The execution agent never pays out above the threshold on its own. It files an
approval request here (and a ticket), and stops. A human support lead reviews
the request with `python main.py approvals list` and decides with
`python main.py approvals approve APR-xxxx --by <name>`. Only then does the
execution agent carry out the payment, citing the approval id.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PENDING, APPROVED, REJECTED, EXECUTED = "pending", "approved", "rejected", "executed"


class ApprovalError(Exception):
    pass


class ApprovalQueue:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        if path and path.exists():
            self._items = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._items, indent=2), encoding="utf-8")

    def request(self, *, kind: str, order_id: str, customer_id: str, amount: float,
                rule_ids: list[str], trace_id: str, ticket_id: str | None, incident: str = "") -> dict[str, Any]:
        with self._lock:
            # one open request per order and kind
            for item in self._items.values():
                if item["order_id"] == order_id and item["kind"] == kind and item["status"] in (PENDING, APPROVED):
                    return item
            approval_id = f"APR-{len(self._items) + 9001}"
            item = {
                "approval_id": approval_id,
                "kind": kind,  # "refund" or "claim"
                "order_id": order_id,
                "customer_id": customer_id,
                "amount": amount,
                "rule_ids": rule_ids,
                "incident": incident,
                "requested_trace_id": trace_id,
                "ticket_id": ticket_id,
                "status": PENDING,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "decided_by": None,
                "decided_at": None,
                "note": "",
            }
            self._items[approval_id] = item
            self._save()
            return item

    def decide(self, approval_id: str, *, approver: str, approve: bool, note: str = "") -> dict[str, Any]:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise ApprovalError(f"Unknown approval id {approval_id}")
            if item["status"] != PENDING:
                raise ApprovalError(f"{approval_id} is already {item['status']}")
            if not approver.strip():
                raise ApprovalError("An approver name is required")
            item.update(status=APPROVED if approve else REJECTED, decided_by=approver,
                        decided_at=datetime.now(timezone.utc).isoformat(), note=note)
            self._save()
            return item

    def mark_executed(self, approval_id: str, result_ref: str) -> None:
        with self._lock:
            item = self._items[approval_id]
            item.update(status=EXECUTED, result_ref=result_ref)
            self._save()

    def get(self, approval_id: str) -> dict[str, Any] | None:
        return self._items.get(approval_id)

    def for_order(self, order_id: str) -> list[dict[str, Any]]:
        return [i for i in self._items.values() if i["order_id"] == order_id]

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        return [i for i in self._items.values() if status is None or i["status"] == status]
