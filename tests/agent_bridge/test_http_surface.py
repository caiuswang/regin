"""Agent-bridge HTTP surface (`web/blueprints/bridge.py`).

The decorator `require_bridge_token` is the SOLE auth guard for these three
endpoints (they join PUBLIC_API_ENDPOINTS, so the JWT gate waves them
through). These tests pin the approved checklist:

  security 1-4 — no header / wrong token / disabled=404 / unconfigured token
                 fails closed (NOT 200),
  behavior 5-8 — POST records a row + calls deliver + persists the outcome,
                 'latest' resolution (reachable → that trace_id; none →
                 structured refusal, not 500), and the GET reads, all
                 token-gated.

`delivery.deliver` is mocked so no test touches tmux. `settings.agent_bridge`
is a live singleton mutated with monkeypatch (the pattern from
`test_delivery.py::_reset_state`). Rows land in the autouse `tmp_db`.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import delivery
from lib.orm.engine import get_connection
from lib.settings import settings

_TOKEN = "s3cret-bridge-token"


# ── helpers ──────────────────────────────────────────────────

def _enable(monkeypatch, *, enabled=True, token=_TOKEN):
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", enabled)
    monkeypatch.setattr(cfg, "token", token)


def _auth(token=_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mock_deliver(monkeypatch, *, delivered=True, detail="delivered to %7"):
    """Replace deliver() with a recorder; returns the call list."""
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


def _rows(trace_id=None):
    conn = get_connection()
    try:
        if trace_id is None:
            cur = conn.execute("SELECT * FROM bridge_messages")
        else:
            cur = conn.execute(
                "SELECT * FROM bridge_messages WHERE trace_id = ?", (trace_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── security 1: no Authorization header → 401 ────────────────

def test_post_without_auth_header_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "hi"})
    assert resp.status_code == 401


# ── security 2: wrong token → 401 ────────────────────────────

def test_post_wrong_token_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "hi"},
                            headers=_auth("not-the-token"))
    assert resp.status_code == 401
    assert _rows() == []  # rejected before any inbox write


# ── security 3: disabled bridge → 404 on POST and both GETs ──

def test_disabled_bridge_404_on_all_routes(anon_client, monkeypatch):
    _enable(monkeypatch, enabled=False)  # token correct, feature off
    _mock_deliver(monkeypatch)
    post = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "hi"},
                            headers=_auth())
    get_sessions = anon_client.get("/api/bridge/sessions", headers=_auth())
    get_messages = anon_client.get("/api/bridge/messages", headers=_auth())
    assert post.status_code == 404
    assert get_sessions.status_code == 404
    assert get_messages.status_code == 404


# ── security 4: unconfigured token + empty bearer → 401 (fail closed) ──

def test_empty_token_and_empty_bearer_fails_closed_401(anon_client, monkeypatch):
    # Bridge enabled but no token configured; caller presents an empty bearer.
    # compare_digest('', '') is True — the guard must NOT let this through.
    _enable(monkeypatch, token="")
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "hi"},
                            headers={"Authorization": "Bearer "})
    assert resp.status_code == 401
    # Also 401 with no header at all when unconfigured.
    resp2 = anon_client.post("/api/bridge/messages",
                             json={"session_id": "T-1", "text": "hi"})
    assert resp2.status_code == 401
    assert _rows() == []


# ── behavior 5: valid POST → 200, row, deliver called, outcome persisted ──

def _post_valid(anon_client, monkeypatch):
    """Enable, mock deliver, POST one valid message; return (resp, calls)."""
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch, delivered=True, detail="delivered to %9")
    resp = anon_client.post(
        "/api/bridge/messages",
        json={"session_id": "T-9", "text": "status please", "sender": "phone"},
        headers=_auth())
    return resp, calls


def test_valid_post_returns_outcome_and_calls_deliver(anon_client, monkeypatch):
    resp, calls = _post_valid(anon_client, monkeypatch)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["delivered"] is True
    assert body["detail"] == "delivered to %9"
    assert isinstance(body["id"], int)
    # deliver() called with (trace_id, text)
    assert calls == [("T-9", "status please")]


def test_valid_post_persists_row_with_outcome(anon_client, monkeypatch):
    _post_valid(anon_client, monkeypatch)
    rows = _rows("T-9")
    assert len(rows) == 1
    row = rows[0]
    assert row["body"] == "status please"
    assert row["sender"] == "phone"
    assert row["delivered"] == 1
    assert row["delivery_detail"] == "delivered to %9"
    assert row["delivery_path"] == "tmux"
    assert row["delivered_at"] is not None


def test_post_missing_text_400(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "   "},
                            headers=_auth())
    assert resp.status_code == 400
    assert _rows() == []


def test_post_missing_session_id_400(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"text": "hi"}, headers=_auth())
    assert resp.status_code == 400
    assert _rows() == []


# ── behavior 6: 'latest' resolution ──────────────────────────

def test_latest_resolves_to_reachable_trace_id(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_reachable_pane(trace_id="T-live")
    calls = _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "latest", "text": "go"},
                            headers=_auth())
    assert resp.status_code == 200
    assert calls == [("T-live", "go")]  # resolved to the reachable session
    assert _rows("T-live")  # inbox row against the resolved trace_id


def test_latest_with_no_reachable_row_structured_refusal(
        anon_client, monkeypatch):
    _enable(monkeypatch)
    calls = _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "latest", "text": "go"},
                            headers=_auth())
    assert resp.status_code == 200  # NOT 500
    body = resp.get_json()
    assert body["delivered"] is False
    assert body["detail"] == "no reachable session"
    assert calls == []  # never attempted delivery
    # the attempt is still recorded, against trace_id="" , delivered=0
    rows = _rows("")
    assert len(rows) == 1
    assert rows[0]["delivered"] == 0
    assert rows[0]["delivery_detail"] == "no reachable session"


# ── behavior 7: GETs return reachable sessions / recorded messages ──

def test_get_sessions_returns_reachable_rows(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_reachable_pane(trace_id="T-a", pane_id="%1", reachable=1)
    _seed_reachable_pane(trace_id="T-b", pane_id="%2", reachable=0)  # excluded
    resp = anon_client.get("/api/bridge/sessions", headers=_auth())
    assert resp.status_code == 200
    trace_ids = {r["trace_id"] for r in resp.get_json()}
    assert trace_ids == {"T-a"}


def test_get_messages_returns_recorded_rows_with_status(
        anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch, delivered=True, detail="ok")
    anon_client.post("/api/bridge/messages",
                     json={"session_id": "T-x", "text": "one"},
                     headers=_auth())
    resp = anon_client.get("/api/bridge/messages", headers=_auth())
    assert resp.status_code == 200
    msgs = resp.get_json()
    assert len(msgs) == 1
    assert msgs[0]["body"] == "one"
    assert msgs[0]["delivered"] == 1
    assert msgs[0]["delivery_detail"] == "ok"


def test_get_messages_session_id_filter(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    for tid in ("T-1", "T-2"):
        anon_client.post("/api/bridge/messages",
                         json={"session_id": tid, "text": "m"},
                         headers=_auth())
    resp = anon_client.get("/api/bridge/messages?session_id=T-2",
                           headers=_auth())
    msgs = resp.get_json()
    assert {m["trace_id"] for m in msgs} == {"T-2"}


# ── security 8: GETs also require the token ───────────────────

def test_get_sessions_requires_token_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    resp = anon_client.get("/api/bridge/sessions")
    assert resp.status_code == 401


def test_get_messages_requires_token_401(anon_client, monkeypatch):
    _enable(monkeypatch)
    resp = anon_client.get("/api/bridge/messages")
    assert resp.status_code == 401


# ── the PUBLIC_API_ENDPOINTS entry is not a silent hole ──────

def test_public_allowlist_entry_still_gated_by_decorator(
        anon_client, monkeypatch):
    """The endpoints are in PUBLIC_API_ENDPOINTS (JWT gate waves them
    through) — proving the decorator, not the gate, is what enforces auth:
    without a token the POST is 401, not accepted."""
    from web.app import PUBLIC_API_ENDPOINTS
    assert "bridge.api_bridge_post_message" in PUBLIC_API_ENDPOINTS
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    resp = anon_client.post("/api/bridge/messages",
                            json={"session_id": "T-1", "text": "hi"})
    assert resp.status_code == 401


# ── input hardening (acceptance item 7) ──────────────────────
# The stored inbox row — not just the typed copy — must be bounded and
# clean (it is rendered in /inbox → latent stored-XSS + unbounded-row DoS).

def test_stored_body_is_sanitized_and_capped(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    cap = settings.agent_bridge.max_text_len
    raw = "\x1b[31mred\x03\x00 line\none\ttwo " + ("A" * (cap + 500))
    resp = anon_client.post(
        "/api/bridge/messages",
        json={"session_id": "T-san", "text": raw}, headers=_auth())
    assert resp.status_code == 200
    rows = _rows("T-san")
    assert len(rows) == 1
    body = rows[0]["body"]
    # no ANSI, no control bytes, newlines/tabs flattened, capped.
    assert "\x1b" not in body and "[31m" not in body
    assert "\x03" not in body and "\x00" not in body
    assert "\n" not in body and "\t" not in body
    assert len(body) <= cap


def test_body_empty_after_sanitize_is_400(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    # pure ANSI/control payload → sanitizes to empty → refuse, no row.
    resp = anon_client.post(
        "/api/bridge/messages",
        json={"session_id": "T-1", "text": "\x1b[0m\x00\x03"},
        headers=_auth())
    assert resp.status_code == 400
    assert _rows() == []


def test_sender_is_clipped_and_stripped(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    raw_sender = ("\x00\x07evil\x1f" + "S" * 9000)
    resp = anon_client.post(
        "/api/bridge/messages",
        json={"session_id": "T-snd", "text": "hi", "sender": raw_sender},
        headers=_auth())
    assert resp.status_code == 200
    rows = _rows("T-snd")
    assert len(rows) == 1
    sender = rows[0]["sender"]
    assert len(sender) <= 200
    assert "\x00" not in sender and "\x07" not in sender and "\x1f" not in sender


def _seed_message_rows(n: int):
    conn = get_connection()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO bridge_messages (trace_id, body, sender) "
                "VALUES (?, ?, ?)", ("T-bulk", f"m{i}", None))
        conn.commit()
    finally:
        conn.close()


def test_limit_negative_is_floored_not_unlimited(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_message_rows(205)
    resp = anon_client.get("/api/bridge/messages?limit=-1", headers=_auth())
    assert resp.status_code == 200
    # -1 is an UNLIMITED LIMIT in SQLite (full-inbox dump of all 205). The
    # max(1, min(-1, 200)) clamp floors it to 1 — the point is it is NOT
    # unbounded; a negative can no longer bypass the cap.
    got = len(resp.get_json())
    assert got == 1
    assert got < 205  # decisively not the full-inbox dump


def test_limit_over_cap_clamped_to_200(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_message_rows(205)
    resp = anon_client.get("/api/bridge/messages?limit=9999", headers=_auth())
    assert resp.status_code == 200
    assert len(resp.get_json()) == 200  # capped at the 200 ceiling


def test_limit_non_int_falls_back_to_default(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_message_rows(60)
    resp = anon_client.get("/api/bridge/messages?limit=abc", headers=_auth())
    assert resp.status_code == 200  # NOT 500
    assert len(resp.get_json()) == 50  # default


def test_limit_explicit_value_respected(anon_client, monkeypatch):
    _enable(monkeypatch)
    _seed_message_rows(20)
    resp = anon_client.get("/api/bridge/messages?limit=5", headers=_auth())
    assert resp.status_code == 200
    assert len(resp.get_json()) == 5


def test_is_test_stamped_under_trace_test_env(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    monkeypatch.setenv("REGIN_TRACE_TEST", "1")
    anon_client.post("/api/bridge/messages",
                     json={"session_id": "T-it", "text": "hi"},
                     headers=_auth())
    rows = _rows("T-it")
    assert len(rows) == 1 and rows[0]["is_test"] == 1


def test_is_test_zero_without_trace_test_env(anon_client, monkeypatch):
    _enable(monkeypatch)
    _mock_deliver(monkeypatch)
    monkeypatch.delenv("REGIN_TRACE_TEST", raising=False)
    anon_client.post("/api/bridge/messages",
                     json={"session_id": "T-real", "text": "hi"},
                     headers=_auth())
    rows = _rows("T-real")
    assert len(rows) == 1 and rows[0]["is_test"] == 0
