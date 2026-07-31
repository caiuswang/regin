"""`agent_sdk.cli_path` resolution.

The value is typed by hand into the settings UI, so it arrives in whatever
shape a person writes a path in — including a tilde, which every other path
setting in regin expands and this one used to hand straight to the launcher.
"""

from __future__ import annotations

import os

from lib.agent_sdk.client import resolve_cli_path
from lib.settings import settings


def test_a_tilde_path_is_expanded_not_passed_through(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "cli_path", "~/bin/claude")

    resolved = resolve_cli_path()

    assert resolved == os.path.expanduser("~/bin/claude")
    assert "~" not in resolved


def test_surrounding_whitespace_is_still_stripped(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "cli_path", "  /opt/claude  ")

    assert resolve_cli_path() == "/opt/claude"


def test_an_absolute_path_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "cli_path", "/opt/homebrew/bin/claude")

    assert resolve_cli_path() == "/opt/homebrew/bin/claude"
