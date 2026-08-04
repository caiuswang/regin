"""The tmux tier of `POST /api/sessions/<id>/bridge-decide` and the read-only
`GET /api/sessions/<id>/bridge-menu` (`web/blueprints/bridge.py`).

A regin-owned session's decide path is pinned in
`tests/agent_sdk/test_decision_routing.py`. These pin the OTHER half: a
session regin only observes over tmux, which has no typed channel, so it
decides by `option_index` and drives the pane's select-TUI through
`delivery.deliver_decision_option` (structured — real hook-captured
options) or `delivery.deliver_live_menu_decision` (`live=true` — a fresh,
re-validated read of the actual screen, for a request like `ExitPlanMode`
that carries no structured options at all; see `lib/agent_bridge
/menu_parse.py`). `bridge-menu` is the read-only peek that fills in the
options for that second case before the operator picks one.
"""

from __future__ import annotations

from lib.agent_bridge import delivery
from lib.settings import settings

_TOKEN = "s3cret-bridge-token"


def _enable(monkeypatch, *, enabled=True):
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", enabled)
    monkeypatch.setattr(cfg, "token", _TOKEN)


def _rows(trace_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM bridge_messages WHERE trace_id = ?", (trace_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _mock_structured(monkeypatch, *, delivered=True, detail="selected in %7"):
    calls: list[tuple] = []

    def _fake(trace_id, option_index):
        calls.append((trace_id, option_index))
        return delivery.DeliveryResult(delivered, detail)

    monkeypatch.setattr(delivery, "deliver_decision_option", _fake)
    return calls


def _mock_live(monkeypatch, *, delivered=True, detail="selected in %7"):
    calls: list[tuple] = []

    def _fake(trace_id, option_index, expect_label=None):
        calls.append((trace_id, option_index, expect_label))
        return delivery.DeliveryResult(delivered, detail)

    monkeypatch.setattr(delivery, "deliver_live_menu_decision", _fake)
    return calls


# ── gate: JWT required, disabled bridge refuses cleanly ──────


def test_decide_anonymous_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_structured(monkeypatch)
    resp = anon_client.post("/api/sessions/T-1/bridge-decide",
                            json={"option_index": 0})
    assert resp.status_code == 401


def test_decide_viewer_403(flask_client, monkeypatch):
    from lib.auth import create_token
    _enable(monkeypatch)
    calls = _mock_structured(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post("/api/sessions/T-1/bridge-decide",
                             json={"option_index": 0}, headers=viewer)
    assert resp.status_code == 403
    assert calls == []
    assert _rows("T-1") == []


def test_decide_disabled_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    calls = _mock_structured(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-decide",
                             json={"option_index": 0})
    assert resp.status_code == 200
    assert resp.get_json() == {"delivered": False, "detail": "bridge disabled"}
    assert calls == []
    assert _rows("T-1") == []


def test_decide_missing_option_index_400(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_structured(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-decide",
                             json={"behavior": "allow"})  # SDK shape, no index
    assert resp.status_code == 400
    assert _rows("T-1") == []


# ── structured path: real hook-captured options ───────────────


def test_decide_structured_option_records_label(flask_client, monkeypatch):
    _enable(monkeypatch)
    calls = _mock_structured(monkeypatch, detail="selected option 1 in %7")
    resp = flask_client.post(
        "/api/sessions/T-a/bridge-decide",
        json={"option_index": 0, "label": "Allow for this session"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is True and isinstance(body["id"], int)
    assert calls == [("T-a", 0)]  # live not passed → structured path
    rows = _rows("T-a")
    assert len(rows) == 1
    assert rows[0]["body"] == "selected Allow for this session"
    assert rows[0]["sender"] == "web:test-editor"
    assert rows[0]["delivered"] == 1


def test_decide_structured_without_label_falls_back_to_ordinal(
        flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_structured(monkeypatch)
    flask_client.post("/api/sessions/T-b/bridge-decide", json={"option_index": 2})
    rows = _rows("T-b")
    assert rows[0]["body"] == "selected option 3"


def test_decide_structured_failure_is_reported_not_raised(flask_client,
                                                            monkeypatch):
    _enable(monkeypatch)
    _mock_structured(monkeypatch, delivered=False, detail="not reachable")
    resp = flask_client.post("/api/sessions/T-c/bridge-decide",
                             json={"option_index": 0})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"delivered": False, "detail": "not reachable", "id": body["id"]}


# ── live-parse path: no structured options (ExitPlanMode today) ──


def test_decide_live_flag_routes_to_live_menu_decision(flask_client, monkeypatch):
    _enable(monkeypatch)
    structured = _mock_structured(monkeypatch)
    live = _mock_live(monkeypatch, detail="selected 'Yes, auto-accept edits' in %7")
    resp = flask_client.post(
        "/api/sessions/T-d/bridge-decide",
        json={"option_index": 0, "live": True, "label": "Yes, auto-accept edits"})
    assert resp.status_code == 200
    assert resp.get_json()["delivered"] is True
    # expect_label carries the operator-seen label through — the TOCTOU
    # guard `deliver_live_menu_decision` checks it against a fresh re-parse.
    assert live == [("T-d", 0, "Yes, auto-accept edits")]
    assert structured == []  # the other path must not also fire


def test_decide_live_without_a_label_passes_no_expect_label(flask_client, monkeypatch):
    live = _mock_live(monkeypatch)
    _enable(monkeypatch)
    flask_client.post("/api/sessions/T-nolabel/bridge-decide",
                      json={"option_index": 2, "live": True})
    assert live == [("T-nolabel", 2, None)]


def test_decide_live_menu_changed_refusal_surfaces_verbatim(flask_client, monkeypatch):
    """A TOCTOU mismatch inside `deliver_live_menu_decision` is a normal
    structured refusal at this layer too — reported, never raised."""
    _enable(monkeypatch)
    _mock_live(monkeypatch, delivered=False,
              detail="menu changed since it was read (expected "
                     "'Yes, auto-accept edits', now 'No thanks'); "
                     "resolve it in the terminal")
    resp = flask_client.post(
        "/api/sessions/T-changed/bridge-decide",
        json={"option_index": 0, "live": True, "label": "Yes, auto-accept edits"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is False
    assert "menu changed" in body["detail"]


def test_decide_live_refusal_surfaces_the_reason_without_raising(
        flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_live(monkeypatch, delivered=False,
              detail="could not reliably read the menu on screen; "
                     "resolve it in the terminal")
    resp = flask_client.post("/api/sessions/T-e/bridge-decide",
                             json={"option_index": 0, "live": True})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is False
    assert "resolve it in the terminal" in body["detail"]


def test_decide_live_false_still_uses_structured_path(flask_client, monkeypatch):
    _enable(monkeypatch)
    structured = _mock_structured(monkeypatch)
    live = _mock_live(monkeypatch)
    flask_client.post("/api/sessions/T-f/bridge-decide",
                      json={"option_index": 1, "live": False})
    assert structured == [("T-f", 1)]
    assert live == []


def test_decide_bridge_token_never_in_response(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_structured(monkeypatch)
    resp = flask_client.post("/api/sessions/T-sec/bridge-decide",
                             json={"option_index": 0})
    assert _TOKEN not in resp.get_data(as_text=True)


# ── an owned session still takes the SDK path (unchanged) ────


def test_decide_owned_session_ignores_option_index_and_uses_typed_channel(
        flask_client, monkeypatch):
    """A regin-owned session must not fall into the tmux branch even when the
    caller sends `option_index` — ownership decides the path, not the
    payload shape."""
    from lib import agent_sdk
    seen = {}

    def _resolve(trace_id, decision, **_kw):
        seen["trace_id"] = trace_id
        seen["decision"] = decision
        return True, "decision delivered"

    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda tid: True)
    monkeypatch.setattr(agent_sdk, "resolve_permission", _resolve)
    structured = _mock_structured(monkeypatch)
    live = _mock_live(monkeypatch)

    resp = flask_client.post(
        "/api/sessions/sdk-owned-1/bridge-decide",
        json={"option_index": 0, "behavior": "allow"})

    assert resp.status_code == 200
    assert seen["trace_id"] == "sdk-owned-1"
    assert seen["decision"]["behavior"] == "allow"
    assert structured == [] and live == []


# ── bridge-menu: read-only live-parsed option list ────────────


def _mock_read_menu(monkeypatch, *, menu=None, detail="ok"):
    calls: list[str] = []

    def _fake(trace_id):
        calls.append(trace_id)
        return menu, detail

    monkeypatch.setattr(delivery, "read_live_menu", _fake)
    return calls


def test_menu_anonymous_401(anon_client):
    resp = anon_client.get("/api/sessions/T-1/bridge-menu")
    assert resp.status_code == 401


def test_menu_viewer_403(flask_client, monkeypatch):
    from lib.auth import create_token
    calls = _mock_read_menu(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.get("/api/sessions/T-1/bridge-menu", headers=viewer)
    assert resp.status_code == 403
    assert calls == []


def test_menu_parsed_returns_options_and_cursor(flask_client, monkeypatch):
    menu = delivery.parse_select_menu(
        " ❯ 1. Yes, auto-accept edits\n   2. Yes, manually approve edits\n")
    _mock_read_menu(monkeypatch, menu=menu, detail="ok")
    resp = flask_client.get("/api/sessions/T-9/bridge-menu")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        "parsed": True,
        "options": ["Yes, auto-accept edits", "Yes, manually approve edits"],
        "cursor_index": 0,
        "detail": "ok",
    }


def test_menu_unparseable_reports_parsed_false_with_detail(flask_client,
                                                            monkeypatch):
    _mock_read_menu(monkeypatch, menu=None,
                    detail="could not reliably read a menu on screen")
    resp = flask_client.get("/api/sessions/T-9/bridge-menu")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "parsed": False,
        "detail": "could not reliably read a menu on screen"}


def test_menu_no_reachable_pane_real_guards(flask_client, monkeypatch):
    """No mock: read_live_menu runs for real and finds no registered pane."""
    _enable(monkeypatch)
    resp = flask_client.get("/api/sessions/T-none/bridge-menu")
    assert resp.status_code == 200
    assert resp.get_json() == {"parsed": False, "detail": "no reachable session"}
