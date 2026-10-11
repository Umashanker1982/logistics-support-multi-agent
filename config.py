"""Central configuration, loaded from environment variables (and an optional .env file).

Nothing in the codebase reads os.environ directly except this module, so every
tunable lives in one place and can be changed per environment without code edits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so we avoid an extra dependency.

    Real environment variables always win over values in the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # "offline" = deterministic rules, no API key needed (default for reviewers).
    # "claude"  = Claude Haiku for intent extraction and response drafting.
    llm_mode: str
    anthropic_api_key: str | None
    model: str
    llm_timeout_s: float
    llm_max_retries: int
    # Prices in USD per million tokens, used to report cost in traces and the benchmark.
    price_input_per_mtok: float
    price_output_per_mtok: float
    # Business / safety rules
    refund_approval_threshold: float
    refund_window_days: int
    max_input_chars: int
    # Storage locations
    data_dir: Path
    memory_dir: Path
    log_dir: Path
    approvals_file: Path


def load_settings() -> Settings:
    key = os.environ.get("ANTHROPIC_API_KEY") or None
    mode = os.environ.get("LLM_MODE", "offline").strip().lower()
    if mode not in {"offline", "claude"}:
        mode = "offline"
    return Settings(
        llm_mode=mode,
        anthropic_api_key=key,
        model=os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"),
        llm_timeout_s=_float("LLM_TIMEOUT_S", 20.0),
        llm_max_retries=_int("LLM_MAX_RETRIES", 2),
        price_input_per_mtok=_float("PRICE_INPUT_PER_MTOK", 0.10),
        price_output_per_mtok=_float("PRICE_OUTPUT_PER_MTOK", 0.50),
        refund_approval_threshold=_float("REFUND_APPROVAL_THRESHOLD", 200.0),
        refund_window_days=_int("REFUND_WINDOW_DAYS", 30),
        max_input_chars=_int("MAX_INPUT_CHARS", 1000),
        data_dir=Path(os.environ.get("DATA_DIR", ROOT / "tools" / "data")),
        memory_dir=Path(os.environ.get("MEMORY_DIR", ROOT / "memory" / "sessions")),
        log_dir=Path(os.environ.get("LOG_DIR", ROOT / "ops" / "logs")),
        approvals_file=Path(os.environ.get("APPROVALS_FILE", ROOT / "security" / "approvals" / "queue.json")),
    )
