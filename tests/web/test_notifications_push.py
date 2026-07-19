"""Realtime badge push: hub fan-out, tickets, stream gating, producer notify.

Fan-out is asserted against a subscriber queue rather than a live stream —
the queue *is* the handoff point, and the generator that drains it blocks for
`KEEPALIVE_SECONDS` by design. The stream endpoint itself is covered through
its first frame, which is all that can be read without waiting on a keepalive.
"""

from __future__ import annotations

import json
import time

import pytest

from lib.agent_messages import store
from lib.notifications import hub, tickets
from lib.notifications.notify import _post_notify as _REAL_POST_NOTIFY


@pytest.fixture(autouse=True)
def _clean_hub():
    hub._subscribers.clear()
    yield
    hub._subscribers.clear()


@pytest.fixture(autouse=True)
def _clean_tickets():
    tickets._tickets.clear()
    yield
    tickets._tickets.clear()


def _seed(body="hi", **kw):
    return store.record_message(trace_id="sess-a", body=body,
                                dispatch_webhook=False, **kw)


def _drain(q):
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    return frames


# ── Counters ────────────────────────────────────────────────────────

def test_counts_start_at_zero(tmp_db):
    assert hub.current_counts() == {"drift_pending": 0, "inbox_unread": 0}


def test_counts_track_the_inbox(tmp_db):
    _seed()
    _seed()
    assert hub.current_counts()["inbox_unread"] == 2


def test_counts_exclude_test_rows(tmp_db):
    _seed()
    _seed(is_test=True)
    assert hub.current_counts()["inbox_unread"] == 1


# ── Fan-out ─────────────────────────────────────────────────────────

def test_broadcast_reaches_every_subscriber(tmp_db):
    a, _ = hub.subscribe()
    b, _ = hub.subscribe()
    _seed()
    hub.broadcast_counts()
    assert _drain(a) == [{"drift_pending": 0, "inbox_unread": 1}]
    assert _drain(b) == [{"drift_pending": 0, "inbox_unread": 1}]


def test_broadcast_with_no_subscribers_is_a_noop(tmp_db):
    hub.broadcast_counts()
    assert hub.subscriber_count() == 0


def test_unsubscribe_stops_delivery(tmp_db):
    q, _ = hub.subscribe()
    hub.unsubscribe(q)
    hub.broadcast_counts()
    assert _drain(q) == []
    assert hub.subscriber_count() == 0


def test_a_slow_subscriber_keeps_the_newest_frame(tmp_db):
    """Frames are absolute counts, so overflowing a queue must discard the
    stale head rather than the fresh tail — the newest describes the whole
    state on its own."""
    q, _ = hub.subscribe()
    for _ in range(hub._QUEUE_DEPTH):
        hub.broadcast_counts()
    _seed()
    hub.broadcast_counts()
    frames = _drain(q)
    assert len(frames) == hub._QUEUE_DEPTH
    assert frames[-1]["inbox_unread"] == 1


def test_a_slow_subscriber_does_not_block_the_others(tmp_db):
    slow, _ = hub.subscribe()
    fast, _ = hub.subscribe()
    for _ in range(hub._QUEUE_DEPTH + 5):
        hub.broadcast_counts()
    _seed()
    hub.broadcast_counts()
    assert _drain(fast)[-1]["inbox_unread"] == 1
    assert _drain(slow)[-1]["inbox_unread"] == 1


# ── Tickets ─────────────────────────────────────────────────────────

def test_ticket_redeems_once(tmp_db):
    ticket = tickets.issue(7)
    assert tickets.redeem(ticket) == 7
    assert tickets.redeem(ticket) is None


def test_expired_ticket_is_refused(tmp_db, monkeypatch):
    monkeypatch.setattr(tickets, "TTL_SECONDS", -1)
    assert tickets.redeem(tickets.issue(7)) is None


def test_redeeming_purges_expired_tickets(tmp_db, monkeypatch):
    monkeypatch.setattr(tickets, "TTL_SECONDS", -1)
    tickets.issue(7)
    tickets.issue(8)
    tickets.redeem("nope")
    assert tickets.outstanding() == 0


def test_unknown_ticket_is_refused(tmp_db):
    assert tickets.redeem("made-up") is None
    assert tickets.redeem("") is None


def test_tickets_are_capped(tmp_db):
    for _ in range(tickets.MAX_OUTSTANDING + 50):
        tickets.issue(1)
    assert tickets.outstanding() == tickets.MAX_OUTSTANDING


def test_the_newest_ticket_survives_overflow(tmp_db):
    for _ in range(tickets.MAX_OUTSTANDING):
        tickets.issue(1)
    newest = tickets.issue(42)
    assert tickets.redeem(newest) == 42


def test_ticket_endpoint_requires_auth(anon_client):
    assert anon_client.post("/api/auth/stream-ticket").status_code == 401


def test_ticket_endpoint_mints_a_redeemable_ticket(flask_client):
    resp = flask_client.post("/api/auth/stream-ticket")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["expires_in"] == tickets.TTL_SECONDS
    assert tickets.redeem(body["ticket"]) == 1


# ── Stream gating ───────────────────────────────────────────────────

def _first_frame(client, ticket):
    resp = client.get(f"/api/notifications/stream?ticket={ticket}",
                      buffered=False)
    try:
        return resp, next(resp.response).decode()
    finally:
        resp.close()


def test_stream_without_a_ticket_is_rejected(anon_client):
    assert anon_client.get("/api/notifications/stream").status_code == 401


def test_stream_with_a_bad_ticket_is_rejected(anon_client):
    resp = anon_client.get("/api/notifications/stream?ticket=nope")
    assert resp.status_code == 401


def test_stream_rejects_a_jwt_in_the_query_string(flask_client):
    """The JWT must not be a stream credential — that is the whole reason
    tickets exist, and a query-string token lands in the access log."""
    from lib.auth import create_token
    token = create_token(1, "test-editor", "admin")
    resp = flask_client.get(f"/api/notifications/stream?ticket={token}")
    assert resp.status_code == 401


def test_stream_ignores_the_authorization_header(flask_client):
    """A bearer header alone must not open the stream — otherwise the ticket
    is decorative."""
    assert flask_client.get("/api/notifications/stream").status_code == 401


def test_stream_opens_with_a_valid_ticket_and_sends_the_counts(
        tmp_db, flask_client):
    _seed()
    ticket = flask_client.post("/api/auth/stream-ticket").get_json()["ticket"]
    resp, frame = _first_frame(flask_client, ticket)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    assert json.loads(frame.removeprefix("data: ")) == {
        "drift_pending": 0, "inbox_unread": 1}


def test_the_stream_spends_its_ticket(tmp_db, flask_client):
    ticket = flask_client.post("/api/auth/stream-ticket").get_json()["ticket"]
    _first_frame(flask_client, ticket)
    assert tickets.redeem(ticket) is None


def test_a_closed_stream_releases_its_subscription(tmp_db, flask_client):
    ticket = flask_client.post("/api/auth/stream-ticket").get_json()["ticket"]
    _first_frame(flask_client, ticket)
    assert hub.subscriber_count() == 0


def test_the_keepalive_is_a_named_event(tmp_db):
    """EventSource never surfaces SSE comments, so a comment keepalive would
    be invisible to the client's staleness check."""
    from web.blueprints import notifications
    assert any(isinstance(c, str) and c.startswith("event: ping")
               for c in notifications._frames.__code__.co_consts)


# ── Loopback trigger ────────────────────────────────────────────────

def test_internal_notify_is_open_to_loopback(anon_client):
    assert anon_client.post("/api/internal/notify").status_code == 200


def test_internal_notify_denies_remote_unauthenticated_callers(anon_client):
    resp = anon_client.post("/api/internal/notify",
                            environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code in (401, 404)


def test_internal_notify_denies_remote_callers_holding_a_token(flask_client):
    resp = flask_client.post("/api/internal/notify",
                             environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 404


def test_internal_notify_pushes_to_subscribers(tmp_db, anon_client):
    q, _ = hub.subscribe()
    _seed()
    anon_client.post("/api/internal/notify")
    assert _drain(q)[-1]["inbox_unread"] == 1


# ── In-process mutations push ───────────────────────────────────────

def test_mark_read_pushes_the_new_count(tmp_db, flask_client):
    message = _seed()
    q, _ = hub.subscribe()
    flask_client.post("/api/agent-messages/read", json={"ids": [message["id"]]})
    assert _drain(q)[-1]["inbox_unread"] == 0


def test_read_all_pushes_the_new_count(tmp_db, flask_client):
    _seed()
    _seed()
    q, _ = hub.subscribe()
    flask_client.post("/api/agent-messages/read-all", json={})
    assert _drain(q)[-1]["inbox_unread"] == 0


# ── Producer-side notify ────────────────────────────────────────────

@pytest.fixture
def notified(monkeypatch):
    """Count loopback notifies. Overrides the suite-wide transport block —
    a per-test setattr is applied after the autouse one, so it wins."""
    calls: list[int] = []
    from lib.notifications import notify
    monkeypatch.setattr(notify, "_post_notify", lambda port: calls.append(port))
    return calls


def test_recording_a_message_notifies(tmp_db, notified):
    _seed()
    assert len(notified) == 1


def test_recording_a_test_message_does_not_notify(tmp_db, notified):
    _seed(is_test=True)
    assert notified == []


def test_dismissing_a_keyed_message_notifies(tmp_db, notified):
    _seed(msg_key="k1")
    notified.clear()
    assert store.dismiss_keyed("sess-a", "k1") == 1
    assert len(notified) == 1


def test_a_no_op_dismiss_does_not_notify(tmp_db, notified):
    assert store.dismiss_keyed("sess-a", "absent") == 0
    assert notified == []


def test_pruning_notifies(tmp_db, notified):
    _seed()
    notified.clear()
    assert store.prune_messages(older_than_days=0) == 1
    assert len(notified) == 1


def test_a_dry_run_prune_does_not_notify(tmp_db, notified):
    _seed()
    notified.clear()
    store.prune_messages(older_than_days=0, dry_run=True)
    assert notified == []


def _age_message(message_id: int, days: int) -> None:
    from datetime import datetime, timedelta
    from lib.orm import SessionLocal
    from lib.orm.models.agent_messages import AgentMessage
    stamp = (datetime.now() - timedelta(days=days)).isoformat()
    with SessionLocal() as session:
        row = session.get(AgentMessage, message_id)
        row.created_at = stamp
        session.add(row)
        session.commit()


def test_the_notify_lands_after_retention_pruning(tmp_db, monkeypatch):
    """Retention hard-deletes, so a notify raised before it would push a
    count the very next read contradicts."""
    from lib.notifications import notify
    from lib.settings import settings
    _age_message(_seed(body="ancient")["id"], days=30)
    monkeypatch.setattr(settings.agent_messages, "retention_days", 1,
                        raising=False)
    observed: list[int] = []
    monkeypatch.setattr(
        notify, "_post_notify",
        lambda _port: observed.append(store.unread_count()))
    _seed(body="fresh")
    assert observed[-1] == 1, "notify saw a pre-prune count"


def test_a_closed_dashboard_costs_the_producer_nothing(tmp_db, monkeypatch):
    """`urlopen` burns its full timeout on a refused port, which would land
    on the user's tool-call latency via the PostToolUse hook.

    Restores the real transport by name rather than with `monkeypatch.undo()`:
    pytest hands the autouse fixtures and the test body the *same* monkeypatch
    instance, so undo() would also disarm `_block_ingest_transport`, the
    external-spawn guard and the `tmp_db` redirect for the rest of this test.
    """
    from lib.notifications import notify
    assert _REAL_POST_NOTIFY.__name__ == "_post_notify", \
        "captured the guard's stub, not the real transport — test is vacuous"
    monkeypatch.setattr(notify, "_post_notify", _REAL_POST_NOTIFY)
    monkeypatch.setattr(notify, "_web_port", lambda: 9)
    started = time.monotonic()
    notify.notify_counts_changed()
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, f"notify took {elapsed * 1000:.0f}ms with nothing listening"


def test_re_arming_the_notify_transport_leaves_the_other_guards_up(
        tmp_db, monkeypatch):
    """A test that restores the real notify transport must not take the
    suite's other isolation with it — pytest shares one monkeypatch with the
    autouse fixtures, so `undo()` here would disarm all of them."""
    import lib.orm.engine as engine_module
    from lib import hook_plugin
    from lib.notifications import notify

    monkeypatch.setattr(notify, "_post_notify", _REAL_POST_NOTIFY)

    assert engine_module.DB_PATH == str(tmp_db), "tmp_db redirect was dropped"
    blocked = hook_plugin._NO_PROXY_OPENER.open(object(), timeout=1)
    assert blocked.__class__.__name__ == "_BlockedResponse", \
        "ingest transport guard was dropped"
    from lib.settings import settings
    assert settings.topic_proposal_external_agents == {}, \
        "external-agent spawn guard was dropped"


def test_subscribing_hands_back_the_current_counts(tmp_db):
    _seed()
    _, first = hub.subscribe()
    assert first == {"drift_pending": 0, "inbox_unread": 1}


def test_frames_arrive_in_the_order_their_counts_were_read(tmp_db):
    """Concurrent broadcasts that read in one order and enqueue in the other
    would leave the badge on the older number, with no tick to correct it."""
    import threading
    q, _ = hub.subscribe()
    seen = []
    reads = iter(range(1, 41))
    real = hub.current_counts

    def slow_read():
        n = next(reads)
        time.sleep(0.001)
        return {"drift_pending": 0, "inbox_unread": n}

    hub.current_counts = slow_read
    try:
        threads = [threading.Thread(target=hub.broadcast_counts)
                   for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        hub.current_counts = real
    while not q.empty():
        seen.append(q.get_nowait()["inbox_unread"])
    assert seen == sorted(seen), f"frames arrived out of order: {seen}"


def test_the_drift_badge_is_pushed_on_ignore(tmp_db, flask_client):
    finding = _seed_drift()
    q, _ = hub.subscribe()
    resp = flask_client.post(f"/api/schema-drift/{finding}/ignore")
    assert resp.status_code == 200
    assert _drain(q)[-1]["drift_pending"] == 0


def test_the_drift_badge_is_pushed_on_delete(tmp_db, flask_client):
    finding = _seed_drift()
    q, _ = hub.subscribe()
    resp = flask_client.delete(f"/api/schema-drift/{finding}")
    assert resp.status_code == 200
    assert _drain(q)[-1]["drift_pending"] == 0


def test_recording_a_drift_finding_notifies(tmp_db, notified):
    _seed_drift()
    assert len(notified) >= 1


def _seed_drift() -> int:
    from sqlalchemy import text
    from lib.orm import SessionLocal
    from lib.trace.payload_drift_store import DriftFinding, record_findings
    record_findings(
        [DriftFinding(agent="claude", tool_name="Bash",
                      drift_kind="unknown_field", field_path="tool_input.x",
                      expected=None, actual_sample="1")],
        {"tool_name": "Bash"})
    with SessionLocal() as session:
        return session.execute(
            text("SELECT id FROM payload_schema_drift LIMIT 1")).scalar_one()
