"""Web-JWT bridge proxy (`bridge.api_session_bridge_send`) + the shallow
map's `bridge_reachable` field (`trace/sessions._bridge_reachability`).

The /live composer never holds the bridge bearer token: the browser POSTs
to `/api/sessions/<id>/bridge-send` under the normal web JWT, and the view
calls the delivery layer in-process. These tests pin:

  gate      — anonymous request 401s (the app-wide JWT gate applies: the
              endpoint is NOT in PUBLIC_API_ENDPOINTS), viewer-role JWT
              403s (require_editor — steering outranks editor mutations),
              disabled bridge is a clean structured refusal
              (delivered=False, "bridge disabled"), never a 404/500,
  refusal   — no reachable pane → structured refusal via the real
              delivery guards (no tmux touched),
  delivered — mocked `delivery.deliver` → outcome + persisted inbox row
              with the web sender label,
  secrecy   — no response body (send or map) ever contains the bridge
              token,
  map       — `bridge_reachable`/`bridge_pane` ride the shallow map:
              False/None when disabled or unregistered, True/pane when a
              reachable pane exists.

`settings.agent_bridge` is the live singleton mutated with monkeypatch,
and panes are seeded straight into `bridge_panes` — both idioms from
`test_http_surface.py`.
"""

from __future__ import annotations

from lib.agent_bridge import commands, delivery
from lib.orm.engine import get_connection
from lib.settings import settings

_TOKEN = "s3cret-bridge-token"


def _enable(monkeypatch, *, enabled=True, token=_TOKEN):
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", enabled)
    monkeypatch.setattr(cfg, "token", token)


def _mock_deliver(monkeypatch, *, delivered=True, detail="delivered to %7"):
    calls: list[tuple[str, str]] = []

    def _fake(trace_id, text):
        calls.append((trace_id, text))
        return delivery.DeliveryResult(delivered, detail)

    monkeypatch.setattr(delivery, "deliver", _fake)
    return calls


def _seed_reachable_pane(trace_id="T-1", pane_id="%7", reachable=1):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, tmux_socket, reachable, cwd) "
            "VALUES (?, ?, 111, 222, NULL, ?, '/work')",
            (trace_id, pane_id, reachable))
        conn.commit()
    finally:
        conn.close()


def _rows(trace_id):
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM bridge_messages WHERE trace_id = ?", (trace_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── gate: JWT required, disabled bridge refuses cleanly ──────


def test_anonymous_send_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/sessions/T-1/bridge-send",
                            json={"text": "hi"})
    assert resp.status_code == 401


def test_viewer_role_send_403(flask_client, monkeypatch):
    """Keystroke injection outranks every editor-gated mutation — a viewer
    JWT must be refused (require_editor), and nothing recorded/delivered."""
    from lib.auth import create_token
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post("/api/sessions/T-1/bridge-send",
                             json={"text": "hi"}, headers=viewer)
    assert resp.status_code == 403
    assert calls == []
    assert _rows("T-1") == []


def test_disabled_bridge_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    calls = _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-send",
                             json={"text": "hi"})
    assert resp.status_code == 200  # clean refusal, not 404/500
    body = resp.get_json()
    assert body == {"delivered": False, "detail": "bridge disabled"}
    assert calls == []          # never reached delivery
    assert _rows("T-1") == []   # and recorded nothing


def test_empty_text_400(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-send",
                             json={"text": "   "})
    assert resp.status_code == 400
    assert _rows("T-1") == []


# ── refusal: no reachable pane (REAL delivery guards, no tmux) ─


def test_no_reachable_pane_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch)  # deliver() runs for real; pane lookup finds nothing
    resp = flask_client.post("/api/sessions/T-none/bridge-send",
                             json={"text": "hello"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is False
    assert body["detail"] == "no reachable session"
    rows = _rows("T-none")
    assert len(rows) == 1 and rows[0]["delivered"] == 0


# ── delivered: mocked tmux delivery ──────────────────────────


def _post_delivered(flask_client, monkeypatch):
    """Enable + mock deliver, POST one message; return (resp, calls)."""
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch, delivered=True, detail="delivered to %9")
    resp = flask_client.post("/api/sessions/T-9/bridge-send",
                             json={"text": "steer left"})
    return resp, calls


def test_delivered_path_returns_outcome(flask_client, monkeypatch):
    resp, calls = _post_delivered(flask_client, monkeypatch)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is True
    assert body["detail"] == "delivered to %9"
    assert isinstance(body["id"], int)
    assert calls == [("T-9", "steer left")]


def test_delivered_path_persists_row(flask_client, monkeypatch):
    _post_delivered(flask_client, monkeypatch)
    rows = _rows("T-9")
    assert len(rows) == 1
    row = rows[0]
    assert row["body"] == "steer left"
    assert row["sender"] == "web:test-editor"  # JWT identity, clipped
    assert row["delivered"] == 1
    assert row["delivery_detail"] == "delivered to %9"


# ── secrecy: the bridge token never appears in any response ──


def test_bridge_token_never_in_send_response(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-sec/bridge-send",
                             json={"text": "hi"})
    assert _TOKEN not in resp.get_data(as_text=True)


def test_bridge_token_never_in_map_response(flask_client, monkeypatch):
    _enable(monkeypatch)
    _seed_reachable_pane(trace_id="T-map")
    resp = flask_client.get("/api/sessions/T-map/map?shallow=1&limit=5")
    assert resp.status_code == 200
    assert _TOKEN not in resp.get_data(as_text=True)


# ── map payload: bridge_reachable rides the shallow poll ─────


def test_map_reachability_false_when_disabled(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    _seed_reachable_pane(trace_id="T-off")  # pane exists, feature off
    body = flask_client.get(
        "/api/sessions/T-off/map?shallow=1&limit=5").get_json()
    assert body["bridge_reachable"] is False
    assert body["bridge_pane"] is None


def test_map_reachability_false_without_pane(flask_client, monkeypatch):
    _enable(monkeypatch)
    body = flask_client.get(
        "/api/sessions/T-nopane/map?shallow=1&limit=5").get_json()
    assert body["bridge_reachable"] is False
    assert body["bridge_pane"] is None


def test_map_reachability_true_with_reachable_pane(flask_client, monkeypatch):
    _enable(monkeypatch)
    _seed_reachable_pane(trace_id="T-on", pane_id="%3")
    body = flask_client.get(
        "/api/sessions/T-on/map?shallow=1&limit=5").get_json()
    assert body["bridge_reachable"] is True
    assert body["bridge_pane"] == "%3"


# ── accept list: /-autocomplete catalog (editor-gated, fail-closed) ──


def test_bridge_commands_anonymous_401(anon_client):
    """Same JWT gate as bridge-send — not in PUBLIC_API_ENDPOINTS."""
    resp = anon_client.get("/api/sessions/T-1/bridge-commands")
    assert resp.status_code == 401


def test_bridge_commands_viewer_403(flask_client, monkeypatch):
    from lib.auth import create_token
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.get("/api/sessions/T-1/bridge-commands",
                            headers=viewer)
    assert resp.status_code == 403


def test_bridge_commands_editor_returns_catalog(flask_client, monkeypatch):
    fixture = [{"name": "deploy", "description": "Ship it.",
                "kind": "command", "scope": "project"}]
    monkeypatch.setattr(commands, "list_session_commands",
                        lambda trace_id: fixture)
    resp = flask_client.get("/api/sessions/T-9/bridge-commands")
    assert resp.status_code == 200
    assert resp.get_json() == {"commands": fixture}


def test_bridge_commands_fail_closed_to_empty(flask_client, monkeypatch):
    """Any enumeration error degrades to {commands: []}, never a 500."""
    def _boom(trace_id):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(commands, "list_session_commands", _boom)
    resp = flask_client.get("/api/sessions/T-9/bridge-commands")
    assert resp.status_code == 200
    assert resp.get_json() == {"commands": []}
