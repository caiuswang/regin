"""`POST /api/tasks/capture` — the API-key-authenticated capture surface.

The endpoint is in PUBLIC_API_ENDPOINTS (the JWT gate waves it through), so
`require_scope` is the only guard; these exercise both the happy path and
every way it must fail closed.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask


def _capture(client, key, body):
    return client.post("/api/tasks/capture", json=body,
                       headers={"Authorization": f"Bearer {key}"})


def _rows():
    with SessionLocal() as session:
        return session.exec(select(AgentTask)).all()


# ── the happy path ───────────────────────────────────────────

def test_valid_key_creates_an_idea_row(anon_client, mint_key, capture_enabled):
    raw, _ = mint_key()
    resp = _capture(anon_client, raw, {
        "title": "ship the capture endpoint",
        "body": "details",
        "source": "telegram",
        "external_id": "tg:1",
        "tags": ["backlog"],
        "links": [{"label": "spec", "href": "https://example.test/spec"}],
    })
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["created"] is True

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].status == "idea"
    assert rows[0].title == "ship the capture endpoint"
    assert rows[0].source == "telegram"
    assert rows[0].external_id == "tg:1"
    assert rows[0].tags_json == '["backlog"]'


def test_replay_resolves_to_the_existing_row(anon_client, mint_key,
                                             capture_enabled):
    """Idempotency is the (source, external_id) constraint, not a time window."""
    raw, _ = mint_key()
    body = {"title": "idea", "source": "telegram", "external_id": "tg:7"}

    first = _capture(anon_client, raw, body)
    second = _capture(anon_client, raw, body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json() == {"id": first.get_json()["id"], "created": False}
    assert len(_rows()) == 1


def test_captures_without_an_external_id_do_not_collide(anon_client, mint_key,
                                                        capture_enabled):
    """NULLs do not collide in a unique index — hand-typed captures coexist."""
    raw, _ = mint_key()
    body = {"title": "idea", "source": "web"}
    assert _capture(anon_client, raw, body).status_code == 201
    assert _capture(anon_client, raw, body).status_code == 201
    assert len(_rows()) == 2


# ── unknown fields are loud, never a silent drop ─────────────

@pytest.mark.parametrize("field", ["cwd", "model", "permission_mode", "status"])
def test_execution_parameters_are_rejected_by_name(anon_client, mint_key,
                                                   capture_enabled, field):
    raw, _ = mint_key()
    resp = _capture(anon_client, raw,
                    {"title": "idea", "source": "web", field: "x"})

    assert resp.status_code == 400
    payload = resp.get_json()
    assert field in payload["error"]
    assert payload["field"] == field
    assert _rows() == []


def test_capture_cannot_choose_its_own_status(anon_client, mint_key,
                                              capture_enabled):
    """`status` is not merely ignored — it is refused, and nothing is stored."""
    raw, _ = mint_key()
    resp = _capture(anon_client, raw,
                    {"title": "idea", "source": "web", "status": "running"})
    assert resp.status_code == 400
    assert _rows() == []


@pytest.mark.parametrize("body,expected_field", [
    ({"source": "web"}, "title"),
    ({"title": "idea"}, "source"),
    ({"title": "x" * 201, "source": "web"}, "title"),
    ({"title": "idea", "body": "b" * 8001, "source": "web"}, "body"),
    ({"title": "idea", "source": "Web Hook"}, "source"),
    ({"title": "idea", "source": "web", "tags": ["t"] * 11}, "tags"),
    ({"title": "idea", "source": "web", "links": [{"label": "no href"}]}, "links"),
])
def test_malformed_bodies_are_400(anon_client, mint_key, capture_enabled,
                                  body, expected_field):
    raw, _ = mint_key()
    resp = _capture(anon_client, raw, body)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == expected_field
    assert _rows() == []


# ── failing closed ───────────────────────────────────────────

def test_missing_key_is_401(anon_client, capture_enabled):
    assert anon_client.post("/api/tasks/capture",
                            json={"title": "x", "source": "web"}
                            ).status_code == 401


def test_unknown_key_is_401(anon_client, capture_enabled):
    assert _capture(anon_client, "rgn_" + "0" * 32,
                    {"title": "x", "source": "web"}).status_code == 401


def test_foreign_scheme_is_401(anon_client, mint_key, capture_enabled):
    """A JWT is not a key: the wrong principal's credential is refused."""
    from lib.auth import create_token
    token = create_token(1, "admin", "admin")
    assert _capture(anon_client, token,
                    {"title": "x", "source": "web"}).status_code == 401


def test_revoked_key_is_401(anon_client, mint_key, capture_enabled):
    from lib import api_keys
    raw, view = mint_key()
    assert api_keys.revoke_key(view["id"]) is True
    assert _capture(anon_client, raw,
                    {"title": "x", "source": "web"}).status_code == 401


def test_key_without_the_scope_is_403(anon_client, mint_key, capture_enabled):
    raw, _ = mint_key(scopes=[])
    assert _capture(anon_client, raw,
                    {"title": "x", "source": "web"}).status_code == 403


def test_capture_disabled_is_404(anon_client, mint_key):
    """Off by default, like the bridge: a valid key still reaches nothing."""
    raw, _ = mint_key()
    assert _capture(anon_client, raw,
                    {"title": "x", "source": "web"}).status_code == 404
    assert _rows() == []


# ── per-key rate limit ───────────────────────────────────────

def test_over_the_per_key_limit_is_429(anon_client, mint_key, monkeypatch):
    from lib.settings import TasksConfig, settings
    monkeypatch.setattr(settings, "tasks", TasksConfig(
        capture_enabled=True, capture_rate_limit_per_minute=2))
    raw, _ = mint_key()

    codes = [_capture(anon_client, raw, {"title": f"i{n}", "source": "web"}
                      ).status_code for n in range(3)]

    assert codes == [201, 201, 429]
    assert len(_rows()) == 2


def test_the_limit_is_per_key_not_global(anon_client, mint_key, monkeypatch):
    from lib.settings import TasksConfig, settings
    monkeypatch.setattr(settings, "tasks", TasksConfig(
        capture_enabled=True, capture_rate_limit_per_minute=1))
    first, _ = mint_key(name="a")
    second, _ = mint_key(name="b")

    assert _capture(anon_client, first, {"title": "a", "source": "web"}
                    ).status_code == 201
    assert _capture(anon_client, first, {"title": "a2", "source": "web"}
                    ).status_code == 429
    assert _capture(anon_client, second, {"title": "b", "source": "web"}
                    ).status_code == 201
