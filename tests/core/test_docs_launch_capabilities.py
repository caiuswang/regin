"""The docs name every field a provider declares for launching a task run."""

from __future__ import annotations

from pathlib import Path

from lib.providers.base import LaunchCapabilities

ROOT = Path(__file__).resolve().parents[2]


def test_docs_name_every_launch_capability_field():
    architecture = (ROOT / "ARCHITECTURE.md").read_text()
    for field in LaunchCapabilities.__annotations__:
        assert f"`{field}`" in architecture, (
            f"ARCHITECTURE.md does not name launch capability `{field}`")


def test_setup_documents_kimi_task_runs():
    setup = (ROOT / "docs/setup.md").read_text()
    for needle in ("kimi login", "ACP", "tmux", "attach"):
        assert needle in setup
    assert "docs/setup.md#run-a-task-with-kimi" in (ROOT / "README.md").read_text()
