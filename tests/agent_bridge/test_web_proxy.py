"""Web-JWT bridge proxy (`bridge.api_session_bridge_send`) + the shallow
map's `bridge_reachable` field (`trace/sessions._bridge_reachability`).

The /live composer never holds the bridge bearer token: the browser POSTs
to `/api/sessions/<id>/bridge-send` under the normal web JWT, and the view
calls the delivery layer in-process. These tests pin:

  suggest   — the `/` accept list and the `@` path suggestions ride the same
              gate and the same fail-closed contract (`{"commands"|"files":
              []}`), `bridge-files` clamps its limit end to end, is confined
              to the session's own cwd for an editor token (no `~`/absolute
              browsing of the host), and answers an unknown session with an
              empty menu rather than regin's own tree,
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

import os
from urllib.parse import quote

from lib.agent_bridge import commands, delivery, files, roots
from lib.orm.engine import get_connection
from lib.settings import settings

_TOKEN = "s3cret-bridge-token"


def _enable(monkeypatch, *, enabled=True, token=_TOKEN):
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", enabled)
    monkeypatch.setattr(cfg, "token", token)


def _mock_deliver(monkeypatch, *, delivered=True, detail="delivered to %7"):
    calls: list[tuple[str, str, bool]] = []

    def _fake(trace_id, text, as_command=False):
        calls.append((trace_id, text, as_command))
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
    assert calls == [("T-9", "steer left", False)]


def test_slash_command_body_routes_as_command(flask_client, monkeypatch):
    # "/exit" sent as a plain message acks "delivered" while the autocomplete
    # menu swallows the Enter — the command shape must take the as_command path.
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-9/bridge-send",
                             json={"text": "/exit"})
    assert resp.status_code == 200
    assert calls == [("T-9", "/exit", True)]


def test_path_like_body_stays_a_plain_message(flask_client, monkeypatch):
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch)
    flask_client.post("/api/sessions/T-9/bridge-send",
                      json={"text": "/Users/x/app.py crashes on boot"})
    assert calls == [("T-9", "/Users/x/app.py crashes on boot", False)]


def test_delivered_path_persists_row(flask_client, monkeypatch):
    _post_delivered(flask_client, monkeypatch)
    rows = _rows("T-9")
    assert len(rows) == 1
    row = rows[0]
    assert row["body"] == "steer left"
    assert row["sender"] == "web:test-editor"  # JWT identity, clipped
    assert row["delivered"] == 1
    assert row["delivery_detail"] == "delivered to %9"


# ── /clear succession: a cleared page still reaches the live pane ──


def _seed_cleared_pane(old="T-old", new="T-new"):
    """The shape `/clear` leaves behind: `old` retired by reason, `new` in
    the pane it held."""
    conn = get_connection()
    try:
        for trace_id, at in ((old, "2026-08-01 10:00:00"),
                             (new, "2026-08-01 11:00:01")):
            conn.execute(
                "INSERT INTO bridge_panes (trace_id, pane_id, "
                "tmux_server_pid, pane_pid, tmux_socket, reachable, cwd, "
                "created_at, updated_at) "
                "VALUES (?, '%7', 111, 222, NULL, 1, '/work', ?, ?)",
                (trace_id, at, at))
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen, "
            "ended_at, ended_reason) VALUES (?, 'ended', ?, ?, ?, 'clear')",
            (old, "2026-08-01 09:00:00", "2026-08-01 11:00:00",
             "2026-08-01 11:00:00"))
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen) "
            "VALUES (?, 'active', ?, ?)",
            (new, "2026-08-01 11:00:01", "2026-08-01 12:00:00"))
        conn.commit()
    finally:
        conn.close()


def test_send_on_a_cleared_session_reaches_its_successor(flask_client,
                                                         monkeypatch):
    """The reported gap: the page the operator is on is over, but the pane
    it named is still occupied by the conversation `/clear` opened."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    calls = _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-old/bridge-send",
                             json={"text": "carry on"})
    assert resp.status_code == 200
    assert calls == [("T-new", "carry on", False)]


def test_the_send_response_names_the_successor(flask_client, monkeypatch):
    """`trace_id` is what lets the composer's page follow the send."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-old/bridge-send",
                             json={"text": "carry on"})
    assert resp.get_json()["trace_id"] == "T-new"


def test_an_ordinary_send_names_the_session_it_was_posted_to(flask_client,
                                                             monkeypatch):
    """No succession, no redirect — and the field is still present, so the
    client has one rule rather than two."""
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-9/bridge-send",
                             json={"text": "hi"})
    assert resp.get_json()["trace_id"] == "T-9"


def test_the_inbox_row_lands_on_the_successor(flask_client, monkeypatch):
    """The row carries the /live queued chip, which belongs on the card the
    operator is about to be looking at."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    _mock_deliver(monkeypatch)
    flask_client.post("/api/sessions/T-old/bridge-send",
                      json={"text": "carry on"})
    assert [r["body"] for r in _rows("T-new")] == ["carry on"]
    assert _rows("T-old") == []


def test_a_slash_command_is_not_carried_into_the_successor(flask_client,
                                                          monkeypatch):
    """`/exit` on a dead page must not exit the live session that replaced it.

    The redirect is right for a prompt and wrong for a command: the operator
    typed it at the conversation they were looking at, and that one is over.
    It goes to its own id and meets the ordinary guards there.
    """
    _enable(monkeypatch)
    _seed_cleared_pane()
    calls = _mock_deliver(monkeypatch)
    resp = flask_client.post("/api/sessions/T-old/bridge-send",
                             json={"text": "/exit"})
    assert calls == [("T-old", "/exit", True)]
    assert resp.get_json()["trace_id"] == "T-old"


def test_a_leading_space_does_not_smuggle_a_command_into_the_successor(
        flask_client, monkeypatch):
    """`is_slash_command` anchors at the first character, so the guard holds
    only because it reads the SANITIZED body — `sanitize_body` strips the
    leading whitespace first. Pinned because that ordering is load-bearing and
    invisible: sanitize before the command test, or `" /exit"` rides the
    redirect into the live successor as a prompt."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    calls = _mock_deliver(monkeypatch)
    for body in (" /exit", "  /clear", "\t/exit", "\n/exit"):
        calls.clear()
        flask_client.post("/api/sessions/T-old/bridge-send",
                          json={"text": body})
        assert [c[0] for c in calls] == ["T-old"], body


def test_a_path_like_body_is_still_a_prompt_and_still_redirects(flask_client,
                                                                monkeypatch):
    """The command guard rides `is_slash_command`, which a path does not
    match — "/Users/x/app.py crashes" is a sentence and follows the send."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    calls = _mock_deliver(monkeypatch)
    flask_client.post("/api/sessions/T-old/bridge-send",
                      json={"text": "/Users/x/app.py crashes on boot"})
    assert calls == [("T-new", "/Users/x/app.py crashes on boot", False)]


def test_interrupt_on_a_cleared_session_is_not_redirected(flask_client,
                                                          monkeypatch):
    """Only a prompt is worth carrying forward. An Escape aimed at a session
    that is already over is aimed at nothing, and delivering it into the
    pane's new occupant cancels a turn nobody asked to cancel — the same
    shape as the stale `/exit` that once ended someone else's session."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    resp = flask_client.post("/api/sessions/T-old/bridge-interrupt")
    body = resp.get_json()
    assert body["delivered"] is False
    assert body["detail"] == "no reachable session"


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


def test_the_map_names_a_cleared_session_s_successor(flask_client,
                                                    monkeypatch):
    """The tmux half of `successor_trace_id` — the field the SDK tier has
    had since `caf8d3b0`, answered from the pane registry instead of an
    in-process runner. It is what keeps the composer on the cleared page."""
    _enable(monkeypatch)
    _seed_cleared_pane()
    body = flask_client.get(
        "/api/sessions/T-old/map?shallow=1&limit=5").get_json()
    assert body["successor_trace_id"] == "T-new"


def test_the_sdk_answer_outranks_the_pane_registry(flask_client, monkeypatch):
    """A run regin owns holds the succession in process; a `bridge_panes`
    row for the same session could only ever be a weaker copy of it."""
    from lib import agent_sdk

    _enable(monkeypatch)
    _seed_cleared_pane()
    monkeypatch.setattr(agent_sdk, "live_child", lambda trace_id: "T-sdk")
    body = flask_client.get(
        "/api/sessions/T-old/map?shallow=1&limit=5").get_json()
    assert body["successor_trace_id"] == "T-sdk"


def test_a_cleared_session_is_reachable_through_its_successor(flask_client,
                                                             monkeypatch):
    """The headline outcome: the composer only renders where the client is
    told regin can steer, and `canFollowUp` reads `bridge_reachable`.

    A cleared session's OWN pane read fails by construction — `_SELF_NOT_ENDED`
    refuses a row whose session has ended — so without the successor fallback
    the page keeps a `successor_trace_id` it has no box to type into.
    """
    _enable(monkeypatch)
    _seed_cleared_pane()
    body = flask_client.get(
        "/api/sessions/T-old/map?shallow=1&limit=5").get_json()
    assert body["bridge_reachable"] is True
    assert body["bridge_pane"] == "%7"


def test_an_ended_session_with_no_successor_is_not_reachable(flask_client,
                                                             monkeypatch):
    """The fallback must not resurrect every dead page — only one whose
    `/clear` left a live conversation in the same pane."""
    _enable(monkeypatch)
    _seed_reachable_pane(trace_id="T-dead")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen, "
            "ended_at, ended_reason) VALUES ('T-dead', 'ended', ?, ?, ?, "
            "'logout')",
            ("2026-08-01 09:00:00", "2026-08-01 10:00:00",
             "2026-08-01 10:00:00"))
        conn.commit()
    finally:
        conn.close()
    body = flask_client.get(
        "/api/sessions/T-dead/map?shallow=1&limit=5").get_json()
    assert body["bridge_reachable"] is False
    assert body["bridge_pane"] is None


def test_a_disabled_bridge_names_no_successor(flask_client, monkeypatch):
    """With the bridge off there is no terminal to reach the successor
    through, so the field the page would act on must not be there."""
    _enable(monkeypatch, enabled=False)
    _seed_cleared_pane()
    body = flask_client.get(
        "/api/sessions/T-old/map?shallow=1&limit=5").get_json()
    assert body["successor_trace_id"] is None


def test_a_live_tmux_session_has_no_successor(flask_client, monkeypatch):
    _enable(monkeypatch)
    _seed_cleared_pane()
    body = flask_client.get(
        "/api/sessions/T-new/map?shallow=1&limit=5").get_json()
    assert body["successor_trace_id"] is None


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


# ── answer proxy: AskUserQuestion answering (editor-gated) ───


def _mock_answer(monkeypatch, *, delivered=True, detail="selected option 2 in %7"):
    calls: list[tuple] = []

    def _fake(trace_id, option_index, free_text=None, expect_chat=False):
        calls.append((trace_id, option_index, free_text, expect_chat))
        return delivery.DeliveryResult(delivered, detail)

    monkeypatch.setattr(delivery, "deliver_answer", _fake)
    return calls


def test_anonymous_answer_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_answer(monkeypatch)
    resp = anon_client.post("/api/sessions/T-1/bridge-answer",
                            json={"option_index": 1})
    assert resp.status_code == 401


def test_viewer_role_answer_403(flask_client, monkeypatch):
    from lib.auth import create_token
    _enable(monkeypatch)
    calls = _mock_answer(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post("/api/sessions/T-1/bridge-answer",
                             json={"option_index": 1}, headers=viewer)
    assert resp.status_code == 403
    assert calls == []
    assert _rows("T-1") == []


def test_answer_disabled_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    calls = _mock_answer(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-answer",
                             json={"option_index": 1})
    assert resp.status_code == 200
    assert resp.get_json() == {"delivered": False, "detail": "bridge disabled"}
    assert calls == []
    assert _rows("T-1") == []


def test_answer_missing_option_index_400(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_answer(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-answer",
                             json={"text": "hi"})  # no option_index
    assert resp.status_code == 400
    assert _rows("T-1") == []


def test_answer_option_records_label(flask_client, monkeypatch):
    """A predefined pick records the human label and passes text=None."""
    _enable(monkeypatch)
    calls = _mock_answer(monkeypatch, detail="selected option 3 in %7")
    resp = flask_client.post(
        "/api/sessions/T-a/bridge-answer",
        json={"option_index": 2, "label": "Use Postgres"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is True and isinstance(body["id"], int)
    assert calls == [("T-a", 2, None, False)]  # option pick → no free text, no chat
    rows = _rows("T-a")
    assert len(rows) == 1
    assert rows[0]["body"] == "Use Postgres"
    assert rows[0]["sender"] == "web:test-editor"
    assert rows[0]["delivered"] == 1


def test_answer_free_text_records_and_passes_text(flask_client, monkeypatch):
    _enable(monkeypatch)
    calls = _mock_answer(monkeypatch, detail="typed answer delivered to %7")
    resp = flask_client.post(
        "/api/sessions/T-b/bridge-answer",
        json={"option_index": 4, "text": "my own answer"})
    assert resp.status_code == 200
    assert calls == [("T-b", 4, "my own answer", False)]
    rows = _rows("T-b")
    assert len(rows) == 1 and rows[0]["body"] == "my own answer"


def test_answer_chat_flag_passes_expect_chat(flask_client, monkeypatch):
    """`chat=true` routes through with expect_chat=True and records the label."""
    _enable(monkeypatch)
    calls = _mock_answer(monkeypatch, detail="selected option 5 in %7")
    resp = flask_client.post(
        "/api/sessions/T-c/bridge-answer",
        json={"option_index": 3, "chat": True, "label": "Chat about this"})
    assert resp.status_code == 200
    assert calls == [("T-c", 3, None, True)]  # chat → expect_chat=True
    rows = _rows("T-c")
    assert len(rows) == 1 and rows[0]["body"] == "Chat about this"


def test_answer_chat_absent_defaults_false(flask_client, monkeypatch):
    """No `chat` key (or a non-True value) leaves expect_chat False."""
    _enable(monkeypatch)
    calls = _mock_answer(monkeypatch)
    flask_client.post("/api/sessions/T-d/bridge-answer",
                      json={"option_index": 0, "label": "yes", "chat": "yes"})
    assert calls == [("T-d", 0, None, False)]


def test_answer_multi_routes_to_deliver_answers(flask_client, monkeypatch):
    """An `answers` array routes to deliver_answers (not the single path) and
    records a joined inbox body of the per-question labels."""
    _enable(monkeypatch)
    seen: list = []

    def _fake(trace_id, answers):
        seen.append((trace_id, answers))
        return delivery.DeliveryResult(True, "submitted 2 answers to %7")

    monkeypatch.setattr(delivery, "deliver_answers", _fake)
    answers = [
        {"option_index": 1, "label": "Search", "confirm_text": "PROBEQ1"},
        {"option_index": 2, "label": "Reset", "confirm_text": "PROBEQ2"},
    ]
    resp = flask_client.post("/api/sessions/T-multi/bridge-answer",
                             json={"answers": answers})
    assert resp.status_code == 200
    assert resp.get_json()["delivered"] is True
    assert seen == [("T-multi", answers)]  # array passed through verbatim
    rows = _rows("T-multi")
    assert len(rows) == 1 and rows[0]["body"] == "Search · Reset"


def test_answer_multi_disabled_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)

    def _fail(*_a, **_k):  # must not be called
        raise AssertionError("deliver_answers called while bridge disabled")

    monkeypatch.setattr(delivery, "deliver_answers", _fail)
    resp = flask_client.post("/api/sessions/T-x/bridge-answer",
                             json={"answers": [{"option_index": 0}]})
    assert resp.status_code == 200
    assert resp.get_json() == {"delivered": False, "detail": "bridge disabled"}
    assert _rows("T-x") == []


def test_answer_bridge_token_never_in_response(flask_client, monkeypatch):
    _enable(monkeypatch)
    _mock_answer(monkeypatch)
    resp = flask_client.post("/api/sessions/T-sec/bridge-answer",
                             json={"option_index": 0, "label": "yes"})
    assert _TOKEN not in resp.get_data(as_text=True)


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


def test_bridge_commands_unresolvable_trace_id_is_empty(flask_client, monkeypatch):
    """Exactly what `bridge-files` does for the same ids. An editor may name
    ANY trace id here, so answering an unregistered / vanished one out of
    regin's own root leaks this host's local command and skill catalog.

    `settings.project_root` is deliberately left pointing at regin's live tree,
    so this is empty *because* there is no fallback.
    """
    assert os.path.isdir(str(settings.project_root))
    for cwd in (None, "", str(settings.project_root / "no-such-dir")):
        monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid, c=cwd: c)
        resp = flask_client.get("/api/sessions/totally-unknown-id/bridge-commands")
        assert resp.status_code == 200
        assert resp.get_json() == {"commands": []}, cwd


def test_bridge_commands_fail_closed_to_empty(flask_client, monkeypatch):
    """Any enumeration error degrades to {commands: []}, never a 500."""
    def _boom(trace_id):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(commands, "list_session_commands", _boom)
    resp = flask_client.get("/api/sessions/T-9/bridge-commands")
    assert resp.status_code == 200
    assert resp.get_json() == {"commands": []}


# ── `@` suggestions: workspace paths (editor-gated, fail-closed) ──


def test_bridge_files_anonymous_401(anon_client):
    resp = anon_client.get("/api/sessions/T-1/bridge-files?q=app")
    assert resp.status_code == 401


def test_bridge_files_viewer_403(flask_client):
    from lib.auth import create_token
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.get("/api/sessions/T-1/bridge-files", headers=viewer)
    assert resp.status_code == 403


def test_bridge_files_editor_returns_rows(flask_client, monkeypatch):
    fixture = [{"path": "src/app.py", "kind": "file"}]
    seen: list[tuple] = []

    def _fake(trace_id, query, limit):
        seen.append((trace_id, query, limit))
        return fixture

    monkeypatch.setattr(files, "list_session_files", _fake)
    resp = flask_client.get("/api/sessions/T-9/bridge-files?q=app&limit=5")
    assert resp.status_code == 200
    assert resp.get_json() == {"files": fixture}
    assert seen == [("T-9", "app", "5")]   # raw args; clamping is the lib's job


def test_bridge_files_defaults_query_and_limit(flask_client, monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(files, "list_session_files",
                        lambda t, q, limit: seen.append((t, q, limit)) or [])
    flask_client.get("/api/sessions/T-9/bridge-files")
    assert seen == [("T-9", "", files.DEFAULT_LIMIT)]


def test_bridge_files_limit_clamped_end_to_end(flask_client, monkeypatch, tmp_path):
    """An absurd `limit` can't drag the whole tree back through the route."""
    for index in range(140):
        (tmp_path / f"item_{index:03d}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(tmp_path))
    resp = flask_client.get("/api/sessions/T-9/bridge-files?q=item&limit=9999")
    assert len(resp.get_json()["files"]) == 100


def test_bridge_files_unresolvable_trace_id_is_empty(flask_client, monkeypatch):
    """An unknown session has no root, so it gets no menu.

    `settings.project_root` is deliberately left pointing at regin's own live
    tree: falling back to it would serve regin's source to a session that is
    not regin's, so this must be empty *because* there is no fallback.
    """
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: None)
    assert os.path.isdir(str(settings.project_root))
    resp = flask_client.get("/api/sessions/T-ghost/bridge-files?q=settings")
    assert resp.status_code == 200
    assert resp.get_json() == {"files": []}


def test_bridge_files_ignore_list_holds_for_a_typed_path(flask_client,
                                                         monkeypatch, tmp_path):
    """An editor typing a path must not reach what the walk refuses to show —
    `.git/`, `.venv/`, `node_modules/`, `__pycache__/` stay unreachable."""
    from lib.auth import create_token
    editor = {"Authorization":
              f"Bearer {create_token(3, 'editor-tester', 'editor')}"}
    cwd = tmp_path / "proj"
    for rel in (".git/hooks/pre-commit", ".git/config", ".venv/bin/python",
                "node_modules/left-pad/index.js", "lib/__pycache__/m.pyc",
                ".claude/commands/deploy.md"):
        path = cwd / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(cwd))
    for query in (".git/", ".git/hooks/", ".venv/", "lib/__pycache__/",
                  "node_modules/"):
        rows = flask_client.get(
            f"/api/sessions/T-9/bridge-files?q={quote(query)}",
            headers=editor).get_json()["files"]
        assert rows == [], f"{query} browsed an ignored directory: {rows}"
    # The allowlisted hidden dir is still reachable — that is what it is for.
    assert flask_client.get(
        "/api/sessions/T-9/bridge-files?q=.claude/commands/",
        headers=editor).get_json()["files"] == [
            {"path": ".claude/commands/deploy.md", "kind": "file"}]


def test_bridge_files_typed_path_reaches_outside_the_session_root(
        flask_client, monkeypatch, tmp_path):
    """A typed path leaves the session cwd, over the route as well as in the
    library — that is the whole point of supporting `../` and absolute paths.

    The boundary this crosses is real and deliberate (see the module docstring
    in `lib/agent_bridge/files.py`): the editor role is now trusted with read
    access to the host, and `require_editor` is what stands between an account
    and that listing. A *viewer* still gets nothing — asserted below, because
    that is the check that must never regress.
    """
    from lib.auth import create_token
    editor = {"Authorization":
              f"Bearer {create_token(3, 'editor-tester', 'editor')}"}
    cwd = tmp_path / "proj"
    (cwd / "src").mkdir(parents=True)
    (cwd / "src" / "app.py").write_text("x", encoding="utf-8")
    (tmp_path / "sibling").mkdir()
    (tmp_path / "sibling" / "shared.py").write_text("y", encoding="utf-8")
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(cwd))

    def _files(query, headers):
        return flask_client.get(
            f"/api/sessions/T-9/bridge-files?q={quote(query)}",
            headers=headers).get_json()["files"]

    assert _files("../sibling/", editor) == [
        {"path": "../sibling/shared.py", "kind": "file"}]
    assert [r["path"] for r in _files("/", editor)] != []
    # A bare term is still a search of the PROJECT, never of the filesystem.
    assert _files("shared", editor) == []

    viewer = {"Authorization":
              f"Bearer {create_token(4, 'viewer-tester', 'viewer')}"}
    assert flask_client.get(
        f"/api/sessions/T-9/bridge-files?q={quote('../sibling/')}",
        headers=viewer).status_code == 403


def test_bridge_files_fail_closed_to_empty(flask_client, monkeypatch):
    def _boom(trace_id, query, limit):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(files, "list_session_files", _boom)
    resp = flask_client.get("/api/sessions/T-9/bridge-files?q=x")
    assert resp.status_code == 200
    assert resp.get_json() == {"files": []}


def test_bridge_files_disabled_structured_refusal(flask_client, monkeypatch):
    _enable(monkeypatch, enabled=False)
    resp = flask_client.get("/api/sessions/T-9/bridge-files?q=x")
    assert resp.status_code == 200
    assert resp.get_json() == {"files": [], "detail": "bridge disabled"}


# ── a session regin launched itself (no pane, ever) ──────────


def _seed_run(trace_id, cwd):
    """An SDK run row — the tier's identity record, written by `lib/agent_sdk`.

    Its child is spawned with `REGIN_BRIDGE=0`, so no `bridge_panes` row exists
    for either of its ids and the pane registry cannot root these menus.
    """
    from lib.agent_sdk import store as sdk_store
    sdk_store.upsert_run(trace_id, status="running", cwd=str(cwd))


def test_bridge_commands_answers_a_spawned_session(flask_client, monkeypatch,
                                                   tmp_path):
    _write = (tmp_path / ".claude" / "commands" / "deploy.md")
    _write.parent.mkdir(parents=True)
    _write.write_text("Ship it.\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _seed_run("sdk-deadbeef", tmp_path)
    rows = flask_client.get(
        "/api/sessions/sdk-deadbeef/bridge-commands").get_json()["commands"]
    assert [r["name"] for r in rows] == ["deploy"]


def test_bridge_files_answers_a_spawned_session(flask_client, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    _seed_run("sdk-deadbeef", tmp_path)
    rows = flask_client.get(
        "/api/sessions/sdk-deadbeef/bridge-files?q=app").get_json()["files"]
    assert rows == [{"path": "src/app.py", "kind": "file"}]


def test_catalogs_ignore_a_disabled_bridge_for_an_owned_session(flask_client,
                                                                monkeypatch,
                                                                tmp_path):
    """`agent_bridge.enabled` authorizes typing into someone's terminal, which
    listing what a regin-owned process accepts never does — same carve-out as
    `bridge-send`. Gating it left an owned run steerable but uncompletable."""
    from lib import agent_sdk
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _enable(monkeypatch, enabled=False)
    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda tid: True)
    _seed_run("sdk-deadbeef", tmp_path)
    files_resp = flask_client.get(
        "/api/sessions/sdk-deadbeef/bridge-files?q=app").get_json()
    commands_resp = flask_client.get(
        "/api/sessions/sdk-deadbeef/bridge-commands").get_json()
    assert files_resp == {"files": [{"path": "src/app.py", "kind": "file"}]}
    assert "detail" not in commands_resp


# ── terminal peek: one-shot raw screen snapshot (editor-gated) ──


def _mock_capture(monkeypatch, *, ok=True, text="", detail="captured %7"):
    calls: list[tuple[str, int | None]] = []

    def _fake(trace_id, lines=None):
        calls.append((trace_id, lines))
        return delivery.CaptureResult(ok, text, detail)

    monkeypatch.setattr(delivery, "capture_screen", _fake)
    return calls


def test_bridge_screen_anonymous_401(anon_client):
    resp = anon_client.get("/api/sessions/T-1/bridge-screen")
    assert resp.status_code == 401


def test_bridge_screen_viewer_403(flask_client, monkeypatch):
    from lib.auth import create_token
    calls = _mock_capture(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.get("/api/sessions/T-1/bridge-screen", headers=viewer)
    assert resp.status_code == 403
    assert calls == []


def test_bridge_screen_no_reachable_pane_structured_refusal(flask_client):
    """No mock: capture_screen runs for real and finds no registered pane."""
    resp = flask_client.get("/api/sessions/T-none/bridge-screen")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"ok": False, "html": "", "detail": "no reachable session"}


def test_bridge_screen_returns_converted_html(flask_client, monkeypatch):
    raw = "plain \x1b[1;38;5;114mgreen bold\x1b[0m tail"
    _mock_capture(monkeypatch, ok=True, text=raw, detail="captured %7")
    resp = flask_client.get("/api/sessions/T-9/bridge-screen")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["detail"] == "captured %7"
    assert "color:#87d787" in body["html"]  # SGR 114 → xterm green
    assert "font-weight:600" in body["html"]
    assert "green bold" in body["html"]


def test_bridge_screen_capture_failure_returns_empty_html(flask_client, monkeypatch):
    """ok=False must not run the (possibly garbage) text through the
    converter — html stays empty rather than rendering a stale/failed read."""
    _mock_capture(monkeypatch, ok=False, text="should not be rendered",
                 detail="stale: pane pid mismatch")
    resp = flask_client.get("/api/sessions/T-9/bridge-screen")
    body = resp.get_json()
    assert body == {"ok": False, "html": "", "detail": "stale: pane pid mismatch"}


def test_bridge_screen_default_is_a_recent_page_not_bare_screen(flask_client, monkeypatch):
    """No `?lines=` still requests a 200-line page of scrollback — a bare
    current-screen-only capture cut off too much recent history."""
    calls = _mock_capture(monkeypatch)
    flask_client.get("/api/sessions/T-9/bridge-screen")
    assert calls == [("T-9", 200)]


def test_bridge_screen_lines_param_overrides_default(flask_client, monkeypatch):
    calls = _mock_capture(monkeypatch)
    flask_client.get("/api/sessions/T-9/bridge-screen?lines=40")
    assert calls == [("T-9", 40)]


def test_bridge_screen_lines_param_capped(flask_client, monkeypatch):
    calls = _mock_capture(monkeypatch)
    flask_client.get("/api/sessions/T-9/bridge-screen?lines=999999")
    assert calls == [("T-9", 2000)]


def test_bridge_screen_lines_param_invalid_falls_back_to_default(flask_client, monkeypatch):
    calls = _mock_capture(monkeypatch)
    flask_client.get("/api/sessions/T-9/bridge-screen?lines=nonsense")
    assert calls == [("T-9", 200)]


# ── cancel the turn: one route, two transports ───────────────


def _mock_key(monkeypatch, *, delivered=True, detail="Escape sent to %7"):
    calls: list[tuple[str, str]] = []

    def _fake(trace_id, key):
        calls.append((trace_id, key))
        return delivery.DeliveryResult(delivered, detail)

    monkeypatch.setattr(delivery, "deliver_key", _fake)
    return calls


def test_interrupt_anonymous_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_key(monkeypatch)
    assert anon_client.post(
        "/api/sessions/T-1/bridge-interrupt").status_code == 401


def test_interrupt_viewer_403(flask_client, monkeypatch):
    """Cancelling someone's running turn outranks every editor-gated
    mutation, so a viewer JWT is refused and nothing is delivered."""
    from lib.auth import create_token
    _enable(monkeypatch)
    calls = _mock_key(monkeypatch)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = flask_client.post("/api/sessions/T-1/bridge-interrupt",
                             headers=viewer)
    assert resp.status_code == 403
    assert calls == []


def test_interrupt_of_a_terminal_session_sends_escape(flask_client,
                                                      monkeypatch):
    """A session regin only traces has no typed channel — the cancel is the
    Escape a human would press in that pane."""
    _enable(monkeypatch)
    calls = _mock_key(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-interrupt")
    assert resp.status_code == 200
    assert resp.get_json() == {"delivered": True,
                               "detail": "Escape sent to %7"}
    assert calls == [("T-1", "Escape")]


def test_interrupt_of_an_owned_run_uses_the_typed_channel(flask_client,
                                                          monkeypatch):
    """No keystrokes and no pane: a run regin launched is interrupted over the
    channel it already holds, and the tmux path is never touched."""
    from lib import agent_sdk
    seen = {}

    def _interrupt(trace_id):
        seen["trace_id"] = trace_id
        return True, "interrupt sent"

    _enable(monkeypatch, enabled=False)
    calls = _mock_key(monkeypatch)
    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda tid: True)
    monkeypatch.setattr(agent_sdk, "interrupt_run", _interrupt)

    resp = flask_client.post("/api/sessions/sdk-deadbeef/bridge-interrupt")

    # Reached even with the bridge off: that flag authorizes typing into
    # someone's terminal, which a control message to our own process is not.
    assert resp.get_json() == {"delivered": True, "detail": "interrupt sent"}
    assert seen["trace_id"] == "sdk-deadbeef"
    assert calls == []


def test_interrupt_disabled_bridge_structured_refusal(flask_client,
                                                      monkeypatch):
    _enable(monkeypatch, enabled=False)
    calls = _mock_key(monkeypatch)
    resp = flask_client.post("/api/sessions/T-1/bridge-interrupt")
    assert resp.status_code == 200
    assert resp.get_json() == {"delivered": False, "detail": "bridge disabled"}
    assert calls == []


def test_interrupt_without_a_reachable_pane_refuses_through_the_real_guards(
        flask_client, monkeypatch):
    """No mock: the delivery guards decide, and nothing touches tmux."""
    _enable(monkeypatch)
    resp = flask_client.post("/api/sessions/T-unregistered/bridge-interrupt")
    body = resp.get_json()
    assert body["delivered"] is False
    assert body["detail"] == "no reachable session"
