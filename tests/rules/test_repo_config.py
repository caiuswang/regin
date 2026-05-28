"""Repo-local `.regin/config.json` overlay (lib/repo_config.py).

A registered repo can extend `language_extensions` for edits inside it
without touching global config or code. Covers load/parse/cache/merge and
the end-to-end routing through the rule_check gate.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from hook_manager.handlers import rule_check
from lib import repo_config
from lib import settings as settings_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    repo_config.reset_cache()
    monkeypatch.setattr(settings_mod.settings, "language_extensions", {})
    yield
    repo_config.reset_cache()


def _write_config(repo_root, data):
    d = repo_root / ".regin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(data))


def test_absent_config_is_empty(tmp_path):
    assert repo_config.load_repo_config(str(tmp_path)).language_extensions == {}


def test_parses_language_extensions(tmp_path):
    _write_config(tmp_path, {"language_extensions": {"kotlin": [".kt", ".kts"]}})
    cfg = repo_config.load_repo_config(str(tmp_path))
    assert cfg.language_extensions == {"kotlin": [".kt", ".kts"]}


def test_invalid_json_is_empty_not_raising(tmp_path):
    d = tmp_path / ".regin"
    d.mkdir()
    (d / "config.json").write_text("{ not valid json")
    # Must not raise — a bad repo config can't be allowed to break the hook.
    assert repo_config.load_repo_config(str(tmp_path)).language_extensions == {}


def test_mtime_cache_reloads_on_change(tmp_path):
    _write_config(tmp_path, {"language_extensions": {"kotlin": [".kt"]}})
    assert repo_config.load_repo_config(str(tmp_path)).language_extensions == {"kotlin": [".kt"]}

    cfg_path = tmp_path / ".regin" / "config.json"
    _write_config(tmp_path, {"language_extensions": {"swift": [".swift"]}})
    # Force a distinct mtime so the change is unambiguous regardless of FS granularity.
    future = time.time() + 5
    os.utime(cfg_path, (future, future))
    assert repo_config.load_repo_config(str(tmp_path)).language_extensions == {"swift": [".swift"]}


def test_effective_merges_repo_over_global(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_mod.settings, "language_extensions",
        {"go": [".go"], "kotlin": [".STALE"]},
    )
    _write_config(tmp_path, {"language_extensions": {"kotlin": [".kt"]}})
    eff = repo_config.effective_language_extensions(str(tmp_path))
    # Repo wins for kotlin; global-only 'go' is preserved.
    assert eff == {"go": [".go"], "kotlin": [".kt"]}


def test_effective_none_repo_returns_global(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "language_extensions", {"go": [".go"]})
    assert repo_config.effective_language_extensions(None) == {"go": [".go"]}


class _StubEngine:
    kind = "grit"

    def __init__(self, engine_id, language_ids):
        self.id = engine_id
        self.language_ids = tuple(language_ids)


def test_repo_config_routes_engine_for_files_inside_repo(tmp_path, monkeypatch):
    _write_config(tmp_path, {"language_extensions": {"kotlin": [".kt"]}})
    engine = _StubEngine("grit", ["kotlin"])
    monkeypatch.setattr(rule_check.rule_engines, "all_engines", lambda: [engine])

    inside = str(tmp_path / "Foo.kt")
    # The repo's overlay routes the kotlin engine to a .kt file inside it.
    assert rule_check._engines_for_file(inside, str(tmp_path)) == [(engine, "kotlin")]
    # The same file with no repo + empty global config matches nothing.
    assert rule_check._engines_for_file(inside, None) == []
