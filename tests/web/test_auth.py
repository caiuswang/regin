"""Unit tests for lib.auth.

Covers password hashing, user CRUD (via SessionLocal → tmp_db),
token issue/verify, and the authenticate() end-to-end flow. Uses
tmp_db so each test starts with a clean user table.
"""

from __future__ import annotations

from lib.auth import (
    authenticate, change_password, create_token, delete_user, get_user,
    hash_password, list_users, register_user, reset_password, set_role,
    update_user, user_count, verify_password, verify_token,
)
from lib import auth as _auth_mod


# ── Hashing round-trip ──────────────────────────────────────

def test_hash_then_verify_succeeds():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h) is True


def test_hashed_passwords_are_unique_per_call():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b  # salt makes each hash unique


def test_verify_rejects_wrong_password():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_verify_rejects_malformed_hash():
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "only:one:colon") is False


# ── Token round-trip ────────────────────────────────────────

def test_token_round_trip():
    token = create_token(42, "alice", "admin")
    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == 42
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"


def test_verify_token_rejects_garbage():
    assert verify_token("not.a.token") is None
    assert verify_token("") is None


# ── User CRUD ───────────────────────────────────────────────

def test_user_count_empty(tmp_db):
    assert user_count() == 0


def test_register_user_first_becomes_admin(tmp_db):
    u = register_user("alice", "Alice", "s3cret")
    assert u["role"] == "admin"
    assert user_count() == 1


def test_register_user_second_is_editor(tmp_db):
    register_user("alice", "Alice", "s3cret")
    u = register_user("bob", "Bob", "s3cret")
    assert u["role"] == "editor"


def test_register_user_explicit_role(tmp_db):
    u = register_user("viewer-user", "Read Only", "p", role="viewer")
    assert u["role"] == "viewer"


def test_list_users_omits_password_hash(tmp_db):
    register_user("alice", "Alice", "s3cret")
    rows = list_users()
    assert len(rows) == 1
    assert "password_hash" not in rows[0]


def test_get_user_by_id(tmp_db):
    u = register_user("alice", "Alice", "s3cret")
    fetched = get_user(u["id"])
    assert fetched["username"] == "alice"
    assert "password_hash" not in fetched


def test_get_user_missing_returns_none(tmp_db):
    assert get_user(9999) is None


def test_update_user_display_name(tmp_db):
    u = register_user("alice", "Alice", "s3cret")
    assert update_user(u["id"], display_name="Alice 2.0") is True
    assert get_user(u["id"])["display_name"] == "Alice 2.0"


def test_update_user_no_fields_is_noop(tmp_db):
    u = register_user("alice", "Alice", "s3cret")
    # Passing only None values returns False without touching the row.
    assert update_user(u["id"]) is False


def test_change_password_requires_old_password(tmp_db):
    u = register_user("alice", "Alice", "old-pw")
    assert change_password(u["id"], "wrong-old", "new-pw") is False
    # After the failed attempt the hash is unchanged.
    assert authenticate("alice", "old-pw") is not None


def test_change_password_success(tmp_db):
    u = register_user("alice", "Alice", "old-pw")
    assert change_password(u["id"], "old-pw", "new-pw") is True
    assert authenticate("alice", "new-pw") is not None
    assert authenticate("alice", "old-pw") is None


def test_reset_password_bypasses_old(tmp_db):
    register_user("alice", "Alice", "old-pw")
    assert reset_password("alice", "forced-reset") is True
    assert authenticate("alice", "forced-reset") is not None


def test_reset_password_missing_user(tmp_db):
    assert reset_password("nobody", "x") is False


def test_set_role_validates_whitelist(tmp_db):
    u = register_user("alice", "Alice", "p")
    assert set_role(u["id"], "editor") is True
    assert get_user(u["id"])["role"] == "editor"
    assert set_role(u["id"], "superuser") is False  # not in whitelist


def test_delete_user_removes_row(tmp_db):
    u = register_user("alice", "Alice", "p")
    assert delete_user(u["id"]) is True
    assert get_user(u["id"]) is None


# ── authenticate end-to-end ─────────────────────────────────

def test_authenticate_returns_token_on_success(tmp_db):
    register_user("alice", "Alice", "s3cret")
    result = authenticate("alice", "s3cret")
    assert result is not None
    assert "token" in result
    payload = verify_token(result["token"])
    assert payload["username"] == "alice"
    assert result["user"]["role"] == "admin"


def test_authenticate_returns_none_on_bad_password(tmp_db):
    register_user("alice", "Alice", "s3cret")
    assert authenticate("alice", "wrong") is None


def test_authenticate_returns_none_on_missing_user(tmp_db):
    assert authenticate("ghost", "x") is None


def test_authenticate_stamps_last_login(tmp_db):
    u = register_user("alice", "Alice", "s3cret")
    assert get_user(u["id"])["last_login"] is None
    authenticate("alice", "s3cret")
    assert get_user(u["id"])["last_login"] is not None


# ── _get_secret fresh-generation ────────────────────────────

def test_get_secret_generates_fresh_when_missing(tmp_path, monkeypatch):
    """First call with no secret file on disk → generate + persist."""
    secret_path = tmp_path / "nested" / "auth.secret"
    monkeypatch.setattr(_auth_mod, "_SECRET_PATH", str(secret_path))
    s = _auth_mod._get_secret()
    assert s  # non-empty
    assert secret_path.read_text().strip() == s
    # Second call reads the persisted value, not a fresh one.
    s2 = _auth_mod._get_secret()
    assert s2 == s


# ── user_count exception branch ─────────────────────────────

def test_user_count_returns_zero_on_db_failure(monkeypatch):
    """Any exception from the session layer → 0 (used for bootstrap)."""
    class _BrokenSession:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(_auth_mod, "AuthSessionLocal",
                         lambda: _BrokenSession())
    assert _auth_mod.user_count() == 0


# ── _user_to_dict include_hash ──────────────────────────────

def test_user_to_dict_include_hash(tmp_db):
    register_user("alice", "Alice", "s3cret")
    from lib.orm.models import User
    from lib.orm import AuthSessionLocal
    from sqlmodel import select
    with AuthSessionLocal() as s:
        u = s.exec(select(User)).first()
    d = _auth_mod._user_to_dict(u, include_hash=True)
    assert "password_hash" in d
    assert d["password_hash"]  # populated


# ── update/delete unknown-id branches ───────────────────────

def test_update_user_unknown_id_returns_false(tmp_db):
    assert update_user(999, display_name="Ghost") is False


def test_delete_user_unknown_id_returns_false(tmp_db):
    assert delete_user(999) is False


# ── Decorator-level branches (via Flask test client) ────────

def test_get_current_user_rejects_invalid_bearer(flask_client, tmp_db):
    """Bearer token that fails verify_token → g._current_user=None → 401."""
    resp = flask_client.post(
        "/api/users/1/role",
        json={"role": "viewer"},
        headers={"Authorization": "Bearer totally-not-a-jwt"},
    )
    # verify_token returns None → get_current_user returns None → 401.
    assert resp.status_code == 401


def test_require_editor_viewer_gets_403(flask_client, tmp_db):
    """A viewer hitting a @require_editor route → 403."""
    token = create_token(5, "viewer-tester", "viewer")
    # POST /api/patterns/create uses @require_editor.
    resp = flask_client.post(
        "/api/patterns/create",
        json={"title": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "Editor role required" in resp.get_json()["error"]


def test_require_role_blocks_non_admin(flask_client, tmp_db):
    """An editor hitting an @require_role('admin') route → 403."""
    token = create_token(5, "editor-tester", "editor")
    resp = flask_client.post(
        "/api/users/1/role",
        json={"role": "viewer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_require_role_unit_rejects_missing_user(monkeypatch):
    """Direct call to require_role decorator when get_current_user
    returns None → 401. The HTTP layer stacks @require_auth ahead of
    @require_role so this branch is only reachable by unit test."""
    from flask import Flask

    app = Flask(__name__)

    @_auth_mod.require_role("admin")
    def _handler():
        return "ok"

    monkeypatch.setattr(_auth_mod, "get_current_user", lambda: None)
    with app.test_request_context("/"):
        resp, status = _handler()
        assert status == 401
        assert "Authentication required" in resp.get_json()["error"]
