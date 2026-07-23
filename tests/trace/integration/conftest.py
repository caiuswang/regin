"""Pytest fixtures for tests/trace/ — tmux-driven session-trace regression tests.

Global preconditions enforced here:
- `tmux` on PATH            (skip suite otherwise)
- `claude` on PATH          (skip suite otherwise)
- Flask server on :8321     (reachable via REGIN_TRACE_API; skip suite otherwise)
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from tests.trace.integration.harness import DEFAULT_API, TraceSession


def _ping_api() -> bool:
    try:
        with urlopen(Request(f"{DEFAULT_API}/sessions"), timeout=2) as r:
            return r.status == 200
    except (URLError, socket.timeout, OSError):
        return False


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: slow-running scenario (skipped by default)")
    config.addinivalue_line(
        "markers",
        "requires_claude: requires the `claude` CLI to be authenticated",
    )


def pytest_collection_modifyitems(config, items):
    skip_slow = pytest.mark.skip(reason="slow scenario; run with --run-slow")
    run_slow = config.getoption("--run-slow", default=False)
    for item in items:
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="Run scenarios tagged @pytest.mark.slow (costs live Claude API calls).",
    )


@pytest.fixture(scope="session", autouse=True)
def _environment_checks():
    missing = []
    if shutil.which("tmux") is None:
        missing.append("tmux")
    if shutil.which("claude") is None:
        missing.append("claude")
    if missing:
        pytest.skip(f"missing prerequisite(s): {', '.join(missing)}", allow_module_level=True)
    if not _ping_api():
        pytest.skip(
            f"Flask API not reachable at {DEFAULT_API}. "
            f"Run `./.venv/bin/python cli/regin.py serve` first.",
            allow_module_level=True,
        )


@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    """Fresh scratch directory per test; seeded with fixtures/ sample files."""
    fixtures = Path(__file__).parent / "fixtures"
    dest = tmp_path / "workspace"
    shutil.copytree(fixtures, dest)
    return dest


@pytest.fixture
def trace_session(tmp_workdir: Path, request):
    """Start a fresh `claude` TraceSession per test and tear it down after.

    The harness itself defaults model=sonnet so this fixture inherits the
    cheap default. Tests that manually construct `TraceSession` (e.g. for
    a non-default permission_mode) also get sonnet unless they pass
    `model=` explicitly.
    """
    ts = TraceSession(workdir=tmp_workdir, test_name=request.node.nodeid)
    ts.start()
    yield ts
    ts.stop()
