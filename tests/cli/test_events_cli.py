"""Unit tests for `regin events list` (the notification-bus catalog CLI)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cli.commands.events import events_app
from lib.agent_messages import events


@pytest.fixture
def runner():
    return CliRunner()


def test_events_list_json_matches_registry(runner):
    result = runner.invoke(events_app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert len(rows) == len(events.REGISTRY)
    assert {r["kind"] for r in rows} == set(events.REGISTRY)


def test_events_list_table_shows_kinds(runner):
    result = runner.invoke(events_app, ["list"])
    assert result.exit_code == 0
    assert "proposal.ready" in result.stdout
    assert "content.drift" in result.stdout
    assert "KIND" in result.stdout
