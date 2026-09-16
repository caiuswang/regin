"""The two structural guarantees behind the capture surface.

1. An API key is a different principal, not a weak user: `require_scope` never
   populates `g._current_user`, so a key fails `require_auth` / `require_editor`
   / `require_role` on every other route.
2. The PUBLIC_API_ENDPOINTS entry is not a silent hole: the gate's public
   branch returns before ANY auth check, which is precisely why the route's
   own decorator has to be — and is — load-bearing.
"""

from __future__ import annotations


def test_require_scope_does_not_populate_current_user(mint_key):
    from flask import Flask, g

    from lib.auth import require_scope

    raw, _ = mint_key()

    @require_scope("task:capture")
    def view():
        return "ok"

    app = Flask(__name__)
    with app.test_request_context(
            "/x", headers={"Authorization": f"Bearer {raw}"}):
        assert view() == "ok"
        # The control: `get_current_user` is what every user gate calls, and
        # nothing on the key path has seeded its cache.
        assert not hasattr(g, "_current_user")
        assert g.api_key["scopes"] == ["task:capture"]


def test_api_key_cannot_reach_a_jwt_gated_route(anon_client, mint_key):
    """`POST /api/agent-runs` is `require_editor`. A key is not an editor."""
    raw, _ = mint_key()
    resp = anon_client.post("/api/agent-runs", json={"prompt": "hi"},
                            headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_api_key_cannot_manage_api_keys(anon_client, mint_key):
    """Not even its own CRUD surface — that is `require_role('admin')`."""
    raw, _ = mint_key()
    headers = {"Authorization": f"Bearer {raw}"}
    assert anon_client.get("/api/api-keys", headers=headers).status_code == 401
    assert anon_client.post("/api/api-keys", json={"name": "x"},
                            headers=headers).status_code == 401


def test_the_capture_decorator_is_the_only_guard(monkeypatch, tmp_db):
    """An undecorated twin on the same allowlist is wide open.

    The gate returns on the PUBLIC branch before it looks at any credential,
    so nothing but `require_scope` stands between an anonymous caller and the
    capture route. Dropping the decorator would not fall back to the JWT gate
    — it would publish the endpoint.
    """
    import web.app as web_app

    app = web_app.create_app()
    app.config["TESTING"] = True

    probe = "tasks.api_capture_probe"
    monkeypatch.setattr(web_app, "PUBLIC_API_ENDPOINTS",
                        web_app.PUBLIC_API_ENDPOINTS | {probe})
    app.add_url_rule("/api/tasks/capture-probe", probe,
                     lambda: ({"reached": True}, 200), methods=["POST"])

    with app.test_client() as client:
        assert client.post("/api/tasks/capture-probe").status_code == 200
        assert client.post("/api/tasks/capture", json={}).status_code == 401


def test_capture_endpoint_is_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS
    assert "tasks.api_capture_task" in PUBLIC_API_ENDPOINTS


def test_api_key_crud_is_not_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS
    assert not {e for e in PUBLIC_API_ENDPOINTS if e.startswith("api_keys.")}
