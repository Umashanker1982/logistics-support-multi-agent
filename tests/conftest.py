from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_MODE"] = "offline"

from agents.orchestrator import Orchestrator, build_components  # noqa: E402
from config import load_settings  # noqa: E402
from tools.backend import Backend  # noqa: E402
from tests.fakes import FakeAnthropic  # noqa: E402


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def backend(settings):
    return Backend.from_dir(settings.data_dir)


@pytest.fixture
def orch(settings, backend):
    """Offline orchestrator with isolated, in-memory state."""
    return Orchestrator(build_components(settings, persistent=False, backend=backend))


@pytest.fixture
def make_claude_orch(settings, backend):
    """Orchestrator whose LLM client is a FakeAnthropic (Claude-mode code paths, no network)."""
    def _make(fake: FakeAnthropic | None = None) -> tuple[Orchestrator, FakeAnthropic]:
        fake = fake or FakeAnthropic()
        components = build_components(settings, persistent=False, backend=backend)
        components.llm.mode = "claude"
        components.llm._client = fake
        return Orchestrator(components), fake
    return _make
