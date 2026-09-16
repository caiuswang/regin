"""`/api/api-keys` — admin-only CRUD, and the once-only plaintext."""

from __future__ import annotations

from lib.auth import create_token


def _create(client, **body):
    return client.post("/api/api-keys", json=body)


def test_admin_can_mint_a_key(flask_client):
    resp = _create(flask_client, name="phone", scopes=["task:capture"])
    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["key"].startswith("rgn_")
    assert len(payload["key"]) == 4 + 32
    assert payload["api_key"]["scopes"] == ["task:capture"]
    assert payload["api_key"]["created_by"] == 1


def test_the_full_key_is_returned_exactly_once(flask_client):
    raw = _create(flask_client, name="phone",
                  scopes=["task:capture"]).get_json()["key"]

    listed = flask_client.get("/api/api-keys").get_json()["keys"]
    assert len(listed) == 1
    row = listed[0]
    assert raw not in str(row)
    assert "key" not in row and "key_hash" not in row
    assert row["key_prefix"] == raw[:8]


def test_unknown_scopes_are_refused(flask_client):
    """A key must not carry a grant nothing enforces."""
    resp = _create(flask_client, name="phone", scopes=["agent:launch"])
    assert resp.status_code == 400


def test_name_is_required(flask_client):
    assert _create(flask_client, scopes=["task:capture"]).status_code == 400


def test_revoke_marks_the_key_and_is_not_repeatable(flask_client):
    key_id = _create(flask_client, name="phone",
                     scopes=["task:capture"]).get_json()["api_key"]["id"]

    assert flask_client.delete(f"/api/api-keys/{key_id}").status_code == 200
    assert flask_client.delete(f"/api/api-keys/{key_id}").status_code == 404

    row = flask_client.get("/api/api-keys").get_json()["keys"][0]
    assert row["revoked"] is True
    assert row["revoked_at"] is not None


def test_revoking_an_unknown_key_is_404(flask_client):
    assert flask_client.delete("/api/api-keys/999").status_code == 404


def test_editor_cannot_manage_keys(flask_client):
    headers = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
    assert flask_client.get("/api/api-keys", headers=headers).status_code == 403
    assert flask_client.post("/api/api-keys", json={"name": "x"},
                             headers=headers).status_code == 403
    assert flask_client.delete("/api/api-keys/1",
                               headers=headers).status_code == 403


def test_anonymous_cannot_manage_keys(anon_client):
    assert anon_client.get("/api/api-keys").status_code == 401
    assert anon_client.post("/api/api-keys", json={"name": "x"}).status_code == 401
