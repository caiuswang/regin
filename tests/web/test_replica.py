"""`regin serve --replica`: a server pointed at a copy of the live DB must not
act on the real world — no boot reap, no background writers, no /api writes."""

from types import SimpleNamespace

import pytest

from cli.commands import server as serve_cmd


@pytest.fixture
def boot_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("web.app._reap_orphaned_agent_runs",
                        lambda: calls.append("reap"))
    monkeypatch.setattr(serve_cmd, "_start_workflow_watcher",
                        lambda debug: calls.append("watcher"))
    monkeypatch.setattr(serve_cmd, "_start_pending_reaper",
                        lambda debug: calls.append("pending_reaper"))
    return calls


@pytest.fixture
def stub_run(monkeypatch):
    import web.app

    real_create_app = web.app.create_app
    seen = SimpleNamespace(replica=None)

    def _create_app(replica=False):
        seen.replica = replica
        app = real_create_app(replica=replica)
        app.run = lambda **kw: None
        return app

    monkeypatch.setattr(web.app, "create_app", _create_app)
    return seen


def _serve(replica):
    serve_cmd.cmd_serve(host="127.0.0.1", port=0, debug=False, replica=replica)


def _client(replica):
    from web.app import create_app
    from lib.auth import create_token

    app = create_app(replica=replica)
    app.config["TESTING"] = True
    client = app.test_client()
    token = create_token(1, "test-editor", "admin")
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client


def test_replica_skips_boot_side_effects(tmp_db, boot_calls, stub_run):
    _serve(replica=True)

    assert stub_run.replica is True
    assert boot_calls == []


def test_replica_refuses_writes_but_allows_login(tmp_db):
    from lib.auth import register_user

    register_user("alice", "Alice", "pw-alice-123")
    client = _client(replica=True)

    for method, path in [("POST", "/api/tags"),
                         ("PUT", "/api/settings"),
                         ("PATCH", "/api/features/1"),
                         ("DELETE", "/api/repos/1"),
                         ("POST", "/api/no-such-route"),
                         ("POST", "/api/auth/register")]:
        resp = client.open(path, method=method, json={})
        assert resp.status_code == 403, (method, path)
        assert resp.get_json() == {"error": "replica"}, (method, path)

    anon = client.application.test_client()
    assert anon.post("/api/tags", json={}).get_json() == {"error": "replica"}

    login = anon.post("/api/auth/login",
                      json={"username": "alice", "password": "pw-alice-123"})
    assert login.status_code == 200
    assert "token" in login.get_json()

    assert client.get("/api/auth/me").status_code == 200
    assert client.head("/api/auth/me").status_code == 200


def test_serve_without_replica_unchanged(tmp_db, boot_calls, stub_run):
    _serve(replica=False)

    assert stub_run.replica is False
    assert boot_calls == ["reap", "watcher", "pending_reaper"]

    client = _client(replica=False)
    resp = client.post("/api/auth/register", json={})
    assert resp.status_code == 400
    assert client.post("/api/auth/login", json={}).status_code == 400
