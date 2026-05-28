"""Unit tests for the settings.json helpers in lib.settings (formerly lib.config).

Covers the JSON read/write helpers, settings merge, path expansion,
save_settings routing (shared/local/auto), and get_current_values
envelope shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import settings as config


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH + SETTINGS_LOCAL_PATH at tmp files."""
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", str(shared))
    monkeypatch.setattr(config, "SETTINGS_LOCAL_PATH", str(local))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    return {"shared": shared, "local": local, "dir": tmp_path}


# ── _load_json ──────────────────────────────────────────────

def test_load_json_missing_returns_empty(tmp_path):
    assert config._load_json(str(tmp_path / "nope.json")) == {}


def test_load_json_invalid_returns_empty(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{ not json")
    assert config._load_json(str(f)) == {}


def test_load_json_valid_returns_dict(tmp_path):
    f = tmp_path / "good.json"
    f.write_text('{"a": 1, "b": "two"}')
    assert config._load_json(str(f)) == {"a": 1, "b": "two"}


# ── _load_settings ──────────────────────────────────────────

def test_load_settings_merges_shared_and_local(tmp_settings):
    tmp_settings["shared"].write_text('{"a": 1, "b": 2}')
    tmp_settings["local"].write_text('{"b": 99, "c": 3}')
    merged = config._load_settings()
    # Local wins on conflicts.
    assert merged == {"a": 1, "b": 99, "c": 3}


def test_load_settings_only_shared(tmp_settings):
    tmp_settings["shared"].write_text('{"only": true}')
    assert config._load_settings() == {"only": True}


def test_load_settings_neither_file_returns_empty(tmp_settings):
    assert config._load_settings() == {}


# ── _expand_paths ───────────────────────────────────────────

def test_expand_paths_string(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config._expand_paths("~/foo") == f"{tmp_path}/foo"


def test_expand_paths_list(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = config._expand_paths(["~/a", "/b", 42])
    assert out == [f"{tmp_path}/a", "/b", 42]


def test_expand_paths_non_string_passthrough():
    assert config._expand_paths(42) == 42
    assert config._expand_paths(None) is None


# ── _get ─────────────────────────────────────────────────────

def test_get_returns_default_when_missing(tmp_settings):
    assert config._get("missing_key", "default-value") == "default-value"


def test_get_returns_value_from_settings(tmp_settings):
    tmp_settings["shared"].write_text('{"mykey": "hello"}')
    assert config._get("mykey", None) == "hello"


def test_get_expands_path_keys(tmp_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    tmp_settings["shared"].write_text('{"skills_dir": "~/skills"}')
    out = config._get("skills_dir", "")
    assert out == f"{tmp_path}/skills"


def test_get_does_not_expand_non_path_keys(tmp_settings):
    tmp_settings["shared"].write_text('{"web_port": 1234}')
    assert config._get("web_port", 0) == 1234


# ── _save_to_file ───────────────────────────────────────────

def test_save_to_file_creates_new_file(tmp_settings):
    config._save_to_file(str(tmp_settings["shared"]), {"a": 1})
    content = json.loads(tmp_settings["shared"].read_text())
    assert content == {"a": 1}


def test_save_to_file_merges_with_existing(tmp_settings):
    tmp_settings["shared"].write_text('{"a": 1}')
    config._save_to_file(str(tmp_settings["shared"]), {"b": 2})
    content = json.loads(tmp_settings["shared"].read_text())
    assert content == {"a": 1, "b": 2}


def test_save_to_file_overwrites_existing_key(tmp_settings):
    tmp_settings["shared"].write_text('{"a": 1}')
    config._save_to_file(str(tmp_settings["shared"]), {"a": 99})
    content = json.loads(tmp_settings["shared"].read_text())
    assert content == {"a": 99}


# ── save_settings ───────────────────────────────────────────

def test_save_settings_auto_routes_local_keys_to_local_file(tmp_settings):
    # skills_dir is a LOCAL key; web_port is shared.
    config.save_settings({
        "skills_dir": "/home/x/skills",
        "web_port": 9000,
    }, scope="auto")

    shared = json.loads(tmp_settings["shared"].read_text())
    local = json.loads(tmp_settings["local"].read_text())
    assert "web_port" in shared
    assert "skills_dir" in local
    assert "skills_dir" not in shared
    assert "web_port" not in local


def test_save_settings_shared_scope_writes_only_shared(tmp_settings):
    config.save_settings({"key": "value"}, scope="shared")
    shared = json.loads(tmp_settings["shared"].read_text())
    assert shared == {"key": "value"}
    assert not tmp_settings["local"].exists()


def test_save_settings_local_scope_writes_only_local(tmp_settings):
    config.save_settings({"key": "value"}, scope="local")
    local = json.loads(tmp_settings["local"].read_text())
    assert local == {"key": "value"}
    assert not tmp_settings["shared"].exists()


def test_save_settings_auto_with_only_shared_keys(tmp_settings):
    config.save_settings({"web_port": 5000}, scope="auto")
    shared = json.loads(tmp_settings["shared"].read_text())
    assert shared == {"web_port": 5000}
    # No local file written.
    assert not tmp_settings["local"].exists()


def test_save_settings_creates_config_dir(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "nested" / "config"
    monkeypatch.setattr(config, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config, "SETTINGS_PATH",
                        str(cfg_dir / "settings.json"))
    monkeypatch.setattr(config, "SETTINGS_LOCAL_PATH",
                        str(cfg_dir / "settings.local.json"))
    config.save_settings({"key": "x"}, scope="shared")
    assert cfg_dir.is_dir()


# ── get_current_values ──────────────────────────────────────

def test_get_current_values_envelope_shape(tmp_settings):
    tmp_settings["shared"].write_text('{"web_port": 9999}')

    out = config.get_current_values()
    # One entry per SETTINGS_SCHEMA row.
    assert len(out) == len(config.SETTINGS_SCHEMA)

    web_port = next(r for r in out if r["key"] == "web_port")
    assert web_port["value"] == 9999
    assert web_port["overridden"] is True
    assert web_port["scope"] == "shared"
    assert web_port["is_list"] is False


def test_get_current_values_marks_local_scope(tmp_settings):
    tmp_settings["local"].write_text('{"skills_dir": "/custom"}')
    out = config.get_current_values()
    skills = next(r for r in out if r["key"] == "skills_dir")
    assert skills["value"] == "/custom"
    assert skills["scope"] == "local"
    assert skills["overridden"] is True


def test_get_current_values_default_untouched(tmp_settings):
    out = config.get_current_values()
    for row in out:
        # Nothing overridden when both files are absent.
        assert row["overridden"] is False
        assert row["value"] == row["default"]


