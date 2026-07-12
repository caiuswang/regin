"""Unit tests for web.blueprints.auth JSON API.

Covers login, register, /auth/me, change-password, profile update,
user management (list/role/delete with role enforcement), and the
offset-paginated audit log endpoint.
"""

from __future__ import annotations

from lib.auth import create_token, register_user


def _auth_header(user_id: int, username: str, role: str) -> dict:
    token = create_token(user_id, username, role)
    return {"Authorization": f"Bearer {token}"}


# ── /api/auth/login ──────────────────────────────────────────

def test_login_requires_username_and_password(flask_client, tmp_db):
    resp = flask_client.post("/api/auth/login", json={})
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"].lower()


def test_login_rejects_invalid_credentials(flask_client, tmp_db):
    resp = flask_client.post("/api/auth/login",
                               json={"username": "x", "password": "y"})
    assert resp.status_code == 401
    assert "Invalid" in resp.get_json()["error"]


def test_login_returns_token_on_success(flask_client, tmp_db):
    register_user("alice", "Alice", "s3cret")
    resp = flask_client.post("/api/auth/login",
                               json={"username": "alice",
                                     "password": "s3cret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "token" in body
    assert body["user"]["username"] == "alice"


# ── /api/auth/register ───────────────────────────────────────

def test_register_requires_fields(flask_client, tmp_db):
    resp = flask_client.post("/api/auth/register", json={})
    assert resp.status_code == 400


def test_register_rejects_short_password(flask_client, tmp_db):
    resp = flask_client.post("/api/auth/register",
                               json={"username": "alice",
                                     "password": "ab"})
    assert resp.status_code == 400
    assert "at least 4" in resp.get_json()["error"]


def test_register_first_user_becomes_admin(flask_client, tmp_db):
    resp = flask_client.post("/api/auth/register",
                               json={"username": "first",
                                     "display_name": "First",
                                     "password": "longenough"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["user"]["role"] == "admin"


def test_register_duplicate_username_returns_409(flask_client, tmp_db):
    register_user("taken", "T", "pw12")
    resp = flask_client.post("/api/auth/register",
                               json={"username": "taken",
                                     "password": "pw12"})
    assert resp.status_code == 409


# ── /api/auth/me ─────────────────────────────────────────────

def test_me_without_auth_signals_needs_setup(anon_client, tmp_db):
    resp = anon_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["user"] is None
    assert body["needs_setup"] is True  # no users exist


def test_me_with_auth_returns_user(flask_client, tmp_db):
    register_user("alice", "Alice", "s3cret")
    resp = flask_client.get("/api/auth/me",
                              headers=_auth_header(1, "alice", "admin"))
    body = resp.get_json()
    assert body["user"]["username"] == "alice"
    assert body["needs_setup"] is False


def test_me_after_registration_no_longer_needs_setup(anon_client, tmp_db):
    register_user("first", "F", "s3cret")
    resp = anon_client.get("/api/auth/me")
    body = resp.get_json()
    assert body["user"] is None
    assert body["needs_setup"] is False  # at least one user exists


# ── /api/auth/change-password ────────────────────────────────

def test_change_password_requires_auth(anon_client, tmp_db):
    resp = anon_client.post("/api/auth/change-password",
                               json={"old_password": "x",
                                     "new_password": "y12345"})
    assert resp.status_code == 401


def test_change_password_requires_both_fields(flask_client, tmp_db):
    user = register_user("u", "U", "oldpass")
    resp = flask_client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpass"},
        headers=_auth_header(user["id"], "u", "admin"),
    )
    assert resp.status_code == 400


def test_change_password_rejects_short_new(flask_client, tmp_db):
    user = register_user("u", "U", "oldpass")
    resp = flask_client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpass", "new_password": "ab"},
        headers=_auth_header(user["id"], "u", "admin"),
    )
    assert resp.status_code == 400


def test_change_password_wrong_old_rejected(flask_client, tmp_db):
    user = register_user("u", "U", "oldpass")
    resp = flask_client.post(
        "/api/auth/change-password",
        json={"old_password": "WRONG", "new_password": "newpass"},
        headers=_auth_header(user["id"], "u", "admin"),
    )
    assert resp.status_code == 400
    assert "incorrect" in resp.get_json()["error"].lower()


def test_change_password_success(flask_client, tmp_db):
    user = register_user("u", "U", "oldpass")
    resp = flask_client.post(
        "/api/auth/change-password",
        json={"old_password": "oldpass", "new_password": "newpass"},
        headers=_auth_header(user["id"], "u", "admin"),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


# ── /api/auth/profile ────────────────────────────────────────

def test_profile_update_requires_auth(anon_client, tmp_db):
    resp = anon_client.post("/api/auth/profile", json={})
    assert resp.status_code == 401


def test_profile_update_success(flask_client, tmp_db):
    user = register_user("u", "U", "pw12")
    resp = flask_client.post(
        "/api/auth/profile",
        json={"display_name": "New Name", "email": "x@y.com"},
        headers=_auth_header(user["id"], "u", "admin"),
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


# ── /api/users (list + role enforcement) ─────────────────────

def test_list_users_requires_auth(anon_client, tmp_db):
    resp = anon_client.get("/api/users")
    assert resp.status_code == 401


def test_list_users_returns_all(flask_client, tmp_db):
    register_user("alpha", "A", "pw12")
    register_user("beta", "B", "pw12")
    resp = flask_client.get("/api/users",
                              headers=_auth_header(1, "alpha", "admin"))
    body = resp.get_json()
    usernames = {u["username"] for u in body}
    assert {"alpha", "beta"} <= usernames


# ── /api/users/<id>/role (admin-only) ────────────────────────

def test_set_role_needs_admin(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")  # role=admin by default
    viewer = register_user("viewer", "V", "pw12", role="viewer")

    resp = flask_client.post(
        f"/api/users/{viewer['id']}/role",
        json={"role": "editor"},
        headers=_auth_header(viewer["id"], "viewer", "viewer"),
    )
    assert resp.status_code == 403


def test_set_role_rejects_invalid_role(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    resp = flask_client.post(
        f"/api/users/{admin['id']}/role",
        json={"role": "superuser"},
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 400


def test_set_role_cannot_change_own(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    resp = flask_client.post(
        f"/api/users/{admin['id']}/role",
        json={"role": "viewer"},
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 400
    assert "own role" in resp.get_json()["error"]


def test_set_role_unknown_user_returns_404(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    resp = flask_client.post(
        "/api/users/99999/role",
        json={"role": "viewer"},
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 404


def test_set_role_success_logs_audit(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    target = register_user("target", "T", "pw12")
    resp = flask_client.post(
        f"/api/users/{target['id']}/role",
        json={"role": "viewer"},
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 200


# ── /api/users/<id>/delete (admin-only) ──────────────────────

def test_delete_user_cannot_delete_self(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    resp = flask_client.post(
        f"/api/users/{admin['id']}/delete",
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 400


def test_delete_user_unknown_returns_404(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    resp = flask_client.post(
        "/api/users/99999/delete",
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 404


def test_delete_user_success(flask_client, tmp_db):
    admin = register_user("admin", "A", "pw12")
    target = register_user("target", "T", "pw12")
    resp = flask_client.post(
        f"/api/users/{target['id']}/delete",
        headers=_auth_header(admin["id"], "admin", "admin"),
    )
    assert resp.status_code == 200
    assert "Deleted" in resp.get_json()["msg"]


# ── /api/audit ──────────────────────────────────────────────

def test_audit_log_empty_returns_paged_envelope(flask_client, tmp_db):
    resp = flask_client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["items"] == []
    # Page.to_envelope nests paging metadata under "pagination".
    assert body["pagination"]["strategy"] == "offset"
    assert body["pagination"]["total"] == 0
    assert body["pagination"]["page"] == 0


# ── Global auth gate (web.app._install_auth_gate) ───────────

def test_gate_blocks_unauthenticated_read(anon_client, tmp_db):
    """A read endpoint with no per-route auth decorator must still reject
    unauthenticated callers — the gate, not the decorator, closes this
    hole (regression: expired tokens used to still read traces/patterns)."""
    resp = anon_client.get("/api/sessions")
    assert resp.status_code == 401
    assert "Authentication" in resp.get_json()["error"]


def test_gate_allows_authenticated_read(flask_client, tmp_db):
    resp = flask_client.get("/api/sessions")
    assert resp.status_code == 200


def test_gate_exempts_machine_ingest(anon_client, tmp_db):
    """Hooks POST trace spans with no JWT, so the ingest endpoint must stay
    public — a 401 here would silently break trace ingestion."""
    resp = anon_client.post("/api/session-spans", json={})
    assert resp.status_code != 401


# ── Registration is admin-only once bootstrapped ─────────────

def test_register_open_only_for_first_user_bootstrap(anon_client, tmp_db):
    """Zero users → registration is public and mints the first admin."""
    resp = anon_client.post("/api/auth/register",
                             json={"username": "root", "password": "longenough"})
    assert resp.status_code == 200
    assert resp.get_json()["user"]["role"] == "admin"


def test_register_after_bootstrap_rejects_anonymous(anon_client, tmp_db):
    """Once any account exists, an unauthenticated register is refused and
    creates nothing — closing the open-registration hole."""
    from lib.auth import list_users
    register_user("root", "R", "pw12")
    resp = anon_client.post("/api/auth/register",
                             json={"username": "intruder", "password": "pw12"})
    assert resp.status_code == 401
    assert not any(u["username"] == "intruder" for u in list_users())


def test_register_after_bootstrap_rejects_non_admin(flask_client, tmp_db):
    from lib.auth import list_users
    register_user("root", "R", "pw12")
    resp = flask_client.post(
        "/api/auth/register",
        json={"username": "intruder", "password": "pw12"},
        headers=_auth_header(2, "ed", "editor"),
    )
    assert resp.status_code == 403
    assert not any(u["username"] == "intruder" for u in list_users())


def test_register_admin_can_create_user_with_role(flask_client, tmp_db):
    admin = register_user("root", "R", "pw12")  # admin by default
    resp = flask_client.post(
        "/api/auth/register",
        json={"username": "newbie", "password": "pw12", "role": "viewer"},
        headers=_auth_header(admin["id"], "root", "admin"),
    )
    assert resp.status_code == 200
    assert resp.get_json()["user"]["role"] == "viewer"


def test_register_admin_rejects_invalid_role(flask_client, tmp_db):
    admin = register_user("root", "R", "pw12")
    resp = flask_client.post(
        "/api/auth/register",
        json={"username": "newbie", "password": "pw12", "role": "superuser"},
        headers=_auth_header(admin["id"], "root", "admin"),
    )
    assert resp.status_code == 400


# ── Session surface is admin-only (ADMIN_API_ENDPOINTS) ──────

def test_sessions_list_forbidden_for_editor(flask_client, tmp_db):
    resp = flask_client.get("/api/sessions",
                             headers=_auth_header(2, "ed", "editor"))
    assert resp.status_code == 403
    assert "Admin" in resp.get_json()["error"]


def test_sessions_list_forbidden_for_viewer(flask_client, tmp_db):
    resp = flask_client.get("/api/sessions",
                             headers=_auth_header(3, "vw", "viewer"))
    assert resp.status_code == 403


def test_session_detail_forbidden_for_non_admin(flask_client, tmp_db):
    """Deep-linking a trace map must not leak content to a non-admin."""
    resp = flask_client.get("/api/sessions/anything/map",
                             headers=_auth_header(2, "ed", "editor"))
    assert resp.status_code == 403


def test_sessions_list_allowed_for_admin(flask_client, tmp_db):
    # flask_client's default identity is admin.
    resp = flask_client.get("/api/sessions")
    assert resp.status_code == 200


def test_session_agent_messages_forbidden_for_non_admin(flask_client, tmp_db):
    """agent-messages returns the session goal text + every send_to_user
    message — the sharpest per-session leak, so it must be admin-only."""
    resp = flask_client.get("/api/sessions/whatever/agent-messages",
                             headers=_auth_header(2, "ed", "editor"))
    assert resp.status_code == 403


def test_session_usage_detail_forbidden_for_non_admin(flask_client, tmp_db):
    for path in ("/api/sessions/x/tool-rollup", "/api/sessions/x/turn-usage"):
        resp = flask_client.get(path, headers=_auth_header(2, "ed", "editor"))
        assert resp.status_code == 403, path
