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


