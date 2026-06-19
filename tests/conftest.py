"""Pytest fixtures: isolate ACTA state in a temp directory per test session."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Configure ACTA to run fully offline against a throwaway data directory before
# any acta module imports/reads settings.
_TMP = Path(tempfile.mkdtemp(prefix="acta-test-"))
os.environ["ACTA_DATA_DIR"] = str(_TMP)
os.environ["ACTA_DEFAULT_PROVIDER"] = "mock"
os.environ.setdefault("ACTA_LOG_LEVEL", "WARNING")

from acta.agents import AgentServices  # noqa: E402
from acta.config import get_settings  # noqa: E402
from acta.orchestrator import Orchestrator  # noqa: E402


@pytest.fixture()
def services(tmp_path) -> AgentServices:
    get_settings.cache_clear()
    os.environ["ACTA_DATA_DIR"] = str(tmp_path)
    settings = get_settings()
    return AgentServices.build(settings)


@pytest.fixture()
def orchestrator(services) -> Orchestrator:
    return Orchestrator(services)
