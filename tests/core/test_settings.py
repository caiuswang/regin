"""Unit tests for lib.settings.

Verifies the pydantic-settings merge order (env > local JSON > shared
JSON > defaults) and the `model_post_init` path-derivation logic that
recomputes downstream paths when REGIN_DATA_DIR changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.settings import Settings, _xdg_data_home


# ── Defaults ─────────────────────────────────────────────────

def test_defaults_pick_xdg_path_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REGIN_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Inline override of the JSON sources so shared/local don't leak.
    monkeypatch.setattr(
        "lib.settings._SHARED_SETTINGS_PATH", tmp_path / "none.json",
    )
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "none2.json",
    )
    s = Settings()
    assert str(s.data_dir).endswith("regin")
    assert str(tmp_path) in str(s.data_dir)


def test_xdg_data_home_falls_back_to_home_local_share(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    p = _xdg_data_home()
    assert str(p).endswith(".local/share")


# ── Env > JSON precedence ────────────────────────────────────

def test_env_wins_over_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_MODE", "shared")
    monkeypatch.setattr(
        "lib.settings._SHARED_SETTINGS_PATH", tmp_path / "none.json",
    )
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "none2.json",
    )
    s = Settings()
    assert s.mode == "shared"


def test_shared_json_overrides_default(tmp_path, monkeypatch):
    monkeypatch.delenv("REGIN_MODE", raising=False)
    shared = tmp_path / "settings.json"
    shared.write_text(json.dumps({"mode": "shared"}))
    monkeypatch.setattr("lib.settings._SHARED_SETTINGS_PATH", shared)
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "none.json",
    )
    s = Settings()
    assert s.mode == "shared"


def test_local_json_overrides_shared(tmp_path, monkeypatch):
    monkeypatch.delenv("REGIN_MODE", raising=False)
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    shared.write_text(json.dumps({"mode": "standalone"}))
    local.write_text(json.dumps({"mode": "shared"}))
    monkeypatch.setattr("lib.settings._SHARED_SETTINGS_PATH", shared)
    monkeypatch.setattr("lib.settings._LOCAL_SETTINGS_PATH", local)
    s = Settings()
    assert s.mode == "shared"


def test_env_wins_over_local(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_MODE", "standalone")
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    shared.write_text(json.dumps({"mode": "shared"}))
    local.write_text(json.dumps({"mode": "shared"}))
    monkeypatch.setattr("lib.settings._SHARED_SETTINGS_PATH", shared)
    monkeypatch.setattr("lib.settings._LOCAL_SETTINGS_PATH", local)
    s = Settings()
    assert s.mode == "standalone"


# ── Path-derivation via REGIN_DATA_DIR ───────────────────────

def test_data_dir_env_recomputes_child_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_DATA_DIR", str(tmp_path / "regin-data"))
    monkeypatch.setattr(
        "lib.settings._SHARED_SETTINGS_PATH", tmp_path / "n1.json",
    )
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "n2.json",
    )
    s = Settings()
    root = tmp_path / "regin-data"
    assert s.data_dir == root
    assert s.patterns_dir == root / "patterns"
    assert s.grit_dir == root / "grit"
    assert s.tags_path == root / "config" / "tags.yaml"


def test_explicit_patterns_dir_overrides_data_dir_derivation(tmp_path, monkeypatch):
    """Setting REGIN_PATTERNS_DIR explicitly wins over the derived
    default — the field keeps the caller-chosen path instead of
    being rewritten relative to REGIN_DATA_DIR."""
    monkeypatch.setenv("REGIN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REGIN_PATTERNS_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(
        "lib.settings._SHARED_SETTINGS_PATH", tmp_path / "n1.json",
    )
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "n2.json",
    )
    s = Settings()
    assert s.patterns_dir == tmp_path / "elsewhere"


def test_tilde_in_json_is_expanded(tmp_path, monkeypatch):
    monkeypatch.delenv("REGIN_SKILLS_DIR", raising=False)
    shared = tmp_path / "settings.json"
    shared.write_text(json.dumps({"skills_dir": "~/some/path"}))
    monkeypatch.setattr("lib.settings._SHARED_SETTINGS_PATH", shared)
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "none.json",
    )
    s = Settings()
    assert "~" not in str(s.skills_dir)
    assert str(s.skills_dir).startswith(str(Path.home()))


# ── Type coercion ────────────────────────────────────────────

def test_web_port_coerced_to_int(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIN_WEB_PORT", "9999")
    monkeypatch.setattr(
        "lib.settings._SHARED_SETTINGS_PATH", tmp_path / "n1.json",
    )
    monkeypatch.setattr(
        "lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "n2.json",
    )
    s = Settings()
    assert s.web_port == 9999
    assert isinstance(s.web_port, int)



def test_topic_proposal_external_agents_parse_from_json(tmp_path, monkeypatch):
    shared = tmp_path / "settings.json"
    shared.write_text(json.dumps({
        "topic_proposal_external_agents": {
            "claude": {
                "command": "claude",
                "args": ["--print"],
                "timeout_seconds": 300,
            },
            "codex": {
                "command": "codex",
                "args": ["exec"],
                "timeout_seconds": 450,
            }
        }
    }))
    monkeypatch.setattr("lib.settings._SHARED_SETTINGS_PATH", shared)
    monkeypatch.setattr("lib.settings._LOCAL_SETTINGS_PATH", tmp_path / "none.json")

    s = Settings()

    assert s.topic_proposal_external_agents["claude"].command == "claude"
    assert s.topic_proposal_external_agents["claude"].args == ["--print"]
    assert s.topic_proposal_external_agents["claude"].timeout_seconds == 300
    assert s.topic_proposal_external_agents["codex"].command == "codex"
    assert s.topic_proposal_external_agents["codex"].args == ["exec"]
    assert s.topic_proposal_external_agents["codex"].timeout_seconds == 450
