"""Typed inputs and outputs shared by every tool."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ToolError(Exception):
    """Raised inside a tool for an expected, user-facing failure (e.g. order not found)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolResult:
    """Every tool returns this envelope, so callers never have to catch exceptions.

    ok      -- True if the tool succeeded.
    data    -- the payload (dict) on success.
    error   -- machine-readable error code on failure, e.g. "ORDER_NOT_FOUND".
    message -- human-readable explanation.
    source  -- citation id for where the data came from, e.g. "ORD-1002" or "POLICY R-4".
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str = ""
    source: str | None = None

    @classmethod
    def success(cls, data: dict[str, Any], source: str | None = None, message: str = "") -> "ToolResult":
        return cls(ok=True, data=data, source=source, message=message)

    @classmethod
    def failure(cls, error: str, message: str) -> "ToolResult":
        return cls(ok=False, error=error, message=message)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
