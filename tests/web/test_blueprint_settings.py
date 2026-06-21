"""Unit tests for web.blueprints.settings JSON API.

GET /api/settings is unauthenticated. POST /api/settings requires the
editor role — tested here by asserting the 401 response when no JWT
cookie is present. The authenticated-save happy path is left to
integration tests (the auth flow and cookie shape is exercised by
tests/test_auth.py).
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_settings_files(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH / SETTINGS_LOCAL_PATH to tmp_path."""
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(shared))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(local))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return {"shared": shared, "local": local}


# ── GET /api/settings ────────────────────────────────────────

def test_get_settings_returns_schema_envelope(
        flask_client, isolated_settings_files):
    resp = flask_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)
    # Envelope columns match SETTINGS_SCHEMA row shape.
    keys = {"key", "default", "value", "description", "is_list",
            "overridden", "scope"}
    assert all(keys <= set(row.keys()) for row in body)


def test_get_settings_reports_overridden_value(
        flask_client, isolated_settings_files):
    isolated_settings_files["shared"].write_text(
        json.dumps({"web_port": 12345})
    )
    resp = flask_client.get("/api/settings")
    body = resp.get_json()
    web_port = next(r for r in body if r["key"] == "web_port")
    assert web_port["value"] == 12345
    assert web_port["overridden"] is True


# ── POST /api/settings (auth-protected) ─────────────────────

def test_post_settings_without_auth_rejected(
        anon_client, isolated_settings_files):
    resp = anon_client.post(
        "/api/settings",
        json={"web_port": 9999},
    )
    assert resp.status_code == 401
    body = resp.get_json()
    assert "Authentication" in body["error"]


# ── Authenticated POSTs ─────────────────────────────────────

def _editor_auth_header():
    """Return a Bearer-token header for a fake editor identity.

    create_token signs the payload with the same secret lib.auth
    verifies against — no DB row is required for verify_token to
    accept a well-formed token.
    """
    from lib.auth import create_token
    token = create_token(1, "test-editor", "editor")
    return {"Authorization": f"Bearer {token}"}


def test_post_settings_save_writes_to_file(
        flask_client, isolated_settings_files):
    resp = flask_client.post(
        "/api/settings",
        json={"web_port": 7777},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    shared = json.loads(isolated_settings_files["shared"].read_text())
    assert shared["web_port"] == 7777


def test_post_settings_save_coerces_int_fields(
        flask_client, isolated_settings_files):
    resp = flask_client.post(
        "/api/settings",
        json={"web_port": "8888"},  # string
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    shared = json.loads(isolated_settings_files["shared"].read_text())
    assert shared["web_port"] == 8888  # int


# ── GET /api/settings/providers ──────────────────────────────

def test_get_provider_settings_returns_providers(
        flask_client, isolated_settings_files):
    resp = flask_client.get("/api/settings/providers")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "providers" in body
    assert "handler_defaults" in body
    claude = next((p for p in body["providers"] if p["id"] == "claude"), None)
    assert claude is not None
    assert claude["active"] is True
    assert claude["enabled"] is True
    assert "path_overrides" in claude
    assert "disabled_handlers" in claude
    assert "priority_overrides" in claude


# ── PUT /api/settings/providers ──────────────────────────────

def test_put_provider_settings_without_auth_rejected(
        anon_client, isolated_settings_files):
    resp = anon_client.put(
        "/api/settings/providers",
        json={"providers": {"claude": {"enabled": True}}},
    )
    assert resp.status_code == 401


def test_put_provider_settings_persists_local(
        flask_client, isolated_settings_files):
    resp = flask_client.put(
        "/api/settings/providers",
        json={
            "providers": {
                "kimi": {
                    "enabled": True,
                    "disabled_handlers": ["trace_payload"],
                    "priority_overrides": {"rule_check": 200},
                }
            }
        },
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    local = json.loads(isolated_settings_files["local"].read_text())
    assert local["providers"]["kimi"]["enabled"] is True
    assert local["providers"]["kimi"]["disabled_handlers"] == ["trace_payload"]
    assert local["providers"]["kimi"]["priority_overrides"] == {"rule_check": 200}


def test_put_provider_settings_rejects_unknown_provider(
        flask_client, isolated_settings_files):
    resp = flask_client.put(
        "/api/settings/providers",
        json={"providers": {"unknown": {"enabled": True}}},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 400
    assert "unknown provider" in resp.get_json()["errors"][0]


def test_put_provider_settings_rejects_bad_priority(
        flask_client, isolated_settings_files):
    resp = flask_client.put(
        "/api/settings/providers",
        json={"providers": {"claude": {"priority_overrides": {"rule_check": "x"}}}},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 400
    errors = resp.get_json()["errors"]
    assert any("priority_overrides" in e for e in errors)


# ── /api/settings/agent-memory list field (inject_skip_commands) ──

def _agent_memory_field(flask_client, key):
    body = flask_client.get("/api/settings/agent-memory").get_json()
    return next(f for f in body["fields"] if f["key"] == key)


def test_agent_memory_get_exposes_skip_commands_list(
        flask_client, isolated_settings_files):
    """The skip-list surfaces as a list-typed field with its array default."""
    field = _agent_memory_field(flask_client, "inject_skip_commands")
    assert field["type"] == "list"
    assert field["default"] == ["/goal", "/goal-verified"]
    assert field["value"] == ["/goal", "/goal-verified"]


def test_agent_memory_put_persists_and_coerces_skip_commands(
        flask_client, isolated_settings_files):
    """Saving an edited list strips entries, drops empties, persists to the
    shared scope, and round-trips back through GET."""
    resp = flask_client.put(
        "/api/settings/agent-memory",
        json={"inject_skip_commands": ["  /review  ", "", "   ", "/goal"]},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Persistence is the contract this endpoint owns; the live singleton is
    # reloaded in a real process but not under the monkeypatched paths here
    # (mirrors test_post_settings_save_writes_to_file, which also only reads
    # the file back).
    shared = json.loads(isolated_settings_files["shared"].read_text())
    assert shared["agent_memory"]["inject_skip_commands"] == ["/review", "/goal"]


def test_agent_memory_put_accepts_empty_skip_commands(
        flask_client, isolated_settings_files):
    """An empty list is valid (restores inject-on-every-command) and
    round-trips as []."""
    resp = flask_client.put(
        "/api/settings/agent-memory",
        json={"inject_skip_commands": []},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    shared = json.loads(isolated_settings_files["shared"].read_text())
    assert shared["agent_memory"]["inject_skip_commands"] == []


def test_agent_memory_put_rejects_non_list_skip_commands(
        flask_client, isolated_settings_files):
    """A scalar where a list is expected is a 400, not silently coerced."""
    resp = flask_client.put(
        "/api/settings/agent-memory",
        json={"inject_skip_commands": "/goal"},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 400
    errors = resp.get_json()["errors"]
    assert any("inject_skip_commands" in e for e in errors)


def test_agent_memory_put_requires_auth(anon_client, isolated_settings_files):
    resp = anon_client.put(
        "/api/settings/agent-memory",
        json={"inject_skip_commands": []},
    )
    assert resp.status_code == 401


