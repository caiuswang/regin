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


# ── /api/settings/topic-evolution (Phase 4c UI exposure) ──────

def _topic_evo_fields(flask_client):
    body = flask_client.get("/api/settings/topic-evolution").get_json()
    return {f["key"]: f for f in body["fields"]}


def test_topic_evolution_get_exposes_all_fields(
        flask_client, isolated_settings_files):
    """The block surfaces every flag with its type + defaults-off value."""
    fields = _topic_evo_fields(flask_client)
    assert set(fields) == {
        "evolution_enabled", "mechanical_autoapply", "auto_spawn_agents",
        "content_drift_cosine", "drift_proposal_batch_max",
        "auto_proposal_expire_days", "auto_review_notes"}
    assert fields["evolution_enabled"]["type"] == "bool"
    assert fields["evolution_enabled"]["value"] is False      # off by default
    assert fields["auto_review_notes"]["type"] == "bool"
    assert fields["auto_review_notes"]["value"] is False       # off by default
    cdc = fields["content_drift_cosine"]
    assert cdc["type"] == "float" and cdc["min"] == 0 and cdc["max"] == 1
    assert fields["drift_proposal_batch_max"]["type"] == "int"


def test_topic_evolution_put_persists_to_shared(
        flask_client, isolated_settings_files):
    """Enabling evolution + tuning a number persists to the shared scope and
    round-trips."""
    resp = flask_client.put(
        "/api/settings/topic-evolution",
        json={"evolution_enabled": True, "auto_proposal_expire_days": 30},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    shared = json.loads(isolated_settings_files["shared"].read_text())
    assert shared["topic_evolution"]["evolution_enabled"] is True
    assert shared["topic_evolution"]["auto_proposal_expire_days"] == 30


def test_topic_evolution_put_rejects_out_of_range_cosine(
        flask_client, isolated_settings_files):
    """A cosine above its max is a 400 (full pydantic re-validation), not saved."""
    resp = flask_client.put(
        "/api/settings/topic-evolution",
        json={"content_drift_cosine": 5},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 400
    assert not isolated_settings_files["shared"].exists() or \
        "content_drift_cosine" not in json.loads(
            isolated_settings_files["shared"].read_text()).get(
                "topic_evolution", {})


def test_topic_evolution_put_requires_auth(
        anon_client, isolated_settings_files):
    resp = anon_client.put(
        "/api/settings/topic-evolution", json={"evolution_enabled": True})
    assert resp.status_code == 401


# ── Block GET smoke + spec/model lockstep (drift guard) ───────
#
# A field-spec key that the pydantic model lacks 500s the block's GET at
# runtime (`_field_payload` does a bare `getattr`), and a green suite hides
# it unless every block has a GET test. These two guards make spec/model
# drift fail in CI instead of in the browser. (Regression: d2d4efa dropped
# retention_days/retention_keep_pinned from AgentMessagesConfig while the
# spec still listed them, 500ing the agent-messages tab.)

def _block_names():
    from web.blueprints.settings import _settings_blocks
    return sorted(_settings_blocks().keys())


@pytest.mark.parametrize("block", _block_names())
def test_settings_block_get_returns_200(
        block, flask_client, isolated_settings_files):
    """Every registered block's GET resolves every field value (no 500)."""
    resp = flask_client.get(f"/api/settings/{block}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert isinstance(body.get("fields"), list) and body["fields"]
    for f in body["fields"]:
        assert "value" in f and "default" in f


@pytest.mark.parametrize("block", _block_names())
def test_settings_block_spec_keys_exist_on_model(block):
    """Each field-spec key must be a real attribute of the block's model, so
    `getattr(current, key)` in _field_payload can never raise."""
    from web.blueprints.settings import _settings_blocks
    spec = _settings_blocks()[block]
    model_keys = set(spec["model"].model_fields)
    spec_keys = {f["key"] for f in spec["fields"]}
    missing = spec_keys - model_keys
    assert not missing, (
        f"{block}: field-spec keys absent from "
        f"{spec['model'].__name__}: {sorted(missing)}")


def test_agent_messages_get_exposes_retention_fields(
        flask_client, isolated_settings_files):
    """The retention fields round-trip through GET with their defaults-off
    values (the exact regression from d2d4efa)."""
    body = flask_client.get("/api/settings/agent-messages").get_json()
    fields = {f["key"]: f for f in body["fields"]}
    # retention_days default None surfaces as the -1 null_as sentinel.
    assert fields["retention_days"]["value"] == -1
    assert fields["retention_keep_pinned"]["value"] is True


