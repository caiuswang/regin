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


def _frames(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _drain(q):
    """The counts payloads a subscriber received, named events filtered out."""
    return [payload for name, payload in _frames(q) if name == hub.COUNTS_EVENT]


def _events(q, name):
    return [payload for ev, payload in _frames(q) if ev == name]


# ── Counters ────────────────────────────────────────────────────────

def test_counts_start_at_zero(tmp_db):
    assert hub.current_counts() == {"drift_pending": 0, "inbox_unread": 0,
                                    "inbox_severity": None}


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
    expected = [{"drift_pending": 0, "inbox_unread": 1,
                 "inbox_severity": "progress"}]
    assert _drain(a) == expected
    assert _drain(b) == expected


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
        "drift_pending": 0, "inbox_unread": 1, "inbox_severity": "progress"}


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
    monkeypatch.setattr(notify, "_post_notify",
                        lambda port, body: calls.append((port, body)))
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
        lambda _port, _body: observed.append(store.unread_count()))
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
    assert first == {"drift_pending": 0, "inbox_unread": 1,
                     "inbox_severity": "progress"}


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
        seen.append(q.get_nowait()[1]["inbox_unread"])
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


# ── Notification events (toasts / blocker banner) ───────────────────

def test_a_recorded_message_is_pushed_as_a_named_event(tmp_db, anon_client):
    message = _seed(body="hello", msg_type="warning", title="Heads up")
    q, _ = hub.subscribe()
    anon_client.post("/api/internal/notify",
                     json={"message_id": message["id"]})
    events = _events(q, "notification")
    assert len(events) == 1
    assert events[0]["id"] == message["id"]
    assert events[0]["msg_type"] == "warning"
    assert events[0]["title"] == "Heads up"


def test_the_message_event_is_read_from_the_store_not_the_body(tmp_db,
                                                               anon_client):
    """The trigger carries an id only, so a forged payload cannot reach a
    stream — the row on the wire is the row in the DB."""
    message = _seed(body="real")
    q, _ = hub.subscribe()
    anon_client.post("/api/internal/notify",
                     json={"message_id": message["id"], "body": "forged",
                           "msg_type": "blocker"})
    event = _events(q, "notification")[0]
    assert event["body"] == "real"
    assert event["msg_type"] == "progress"


def test_a_test_row_is_not_pushed_to_the_surfaces(tmp_db, anon_client):
    """Test rows are outside the badge's scope, so they must not toast."""
    message = _seed(body="synthetic", is_test=True)
    q, _ = hub.subscribe()
    anon_client.post("/api/internal/notify",
                     json={"message_id": message["id"]})
    assert _events(q, "notification") == []


def test_a_vanished_row_pushes_no_event_but_still_corrects_the_badge(
        tmp_db, anon_client):
    q, _ = hub.subscribe()
    resp = anon_client.post("/api/internal/notify", json={"message_id": 9999})
    assert resp.status_code == 200
    assert [name for name, _ in _frames(q)] == [hub.COUNTS_EVENT]


def test_a_bare_trigger_still_pushes_counts_only(tmp_db, anon_client):
    _seed()
    q, _ = hub.subscribe()
    anon_client.post("/api/internal/notify")
    frames = _frames(q)
    assert [name for name, _ in frames] == [hub.COUNTS_EVENT]
    assert frames[-1][1]["inbox_unread"] == 1


def test_marking_read_retires_as_read_not_dismissed(tmp_db, flask_client):
    """Reading is not answering: the client retires the toast on `read` but
    must keep a blocker banner up, so the reason has to travel with the frame."""
    message = _seed()
    q, _ = hub.subscribe()
    flask_client.post("/api/agent-messages/read", json={"ids": [message["id"]]})
    assert _events(q, "resolved") == [
        {"message_ids": [message["id"]], "reason": "read"}]


def test_read_all_retires_every_surface_as_read(tmp_db, flask_client):
    _seed()
    q, _ = hub.subscribe()
    flask_client.post("/api/agent-messages/read-all", json={})
    assert _events(q, "resolved") == [{"all": True, "reason": "read"}]


def test_acking_retires_as_read(tmp_db, flask_client):
    message = _seed()
    q, _ = hub.subscribe()
    flask_client.post(f"/api/agent-messages/{message['id']}/ack")
    assert _events(q, "resolved") == [
        {"message_ids": [message["id"]], "reason": "read"}]


def test_dismissing_a_card_retires_as_hidden_not_dismissed(tmp_db, flask_client):
    """Hiding a card is the human closing a surface, not the agent being
    un-parked. Only `dismiss_keyed` — the prompt actually resolving — may claim
    `dismissed`, because that is what paints the session "resumed"."""
    message = _seed()
    q, _ = hub.subscribe()
    flask_client.post(f"/api/agent-messages/{message['id']}/dismiss")
    assert _events(q, "resolved") == [
        {"message_ids": [message["id"]], "reason": "hidden"}]


def test_every_retire_reason_is_one_the_client_knows(tmp_db, flask_client):
    """The client fails closed on an unknown reason — a blocker it cannot
    classify stays up — so a producer inventing a reason would silently stop
    retiring banners. Pin the vocabulary here rather than discover it there."""
    known = {"read", "hidden", "dismissed"}
    message = _seed(msg_key="k9")
    q, _ = hub.subscribe()
    flask_client.post("/api/agent-messages/read", json={"ids": [message["id"]]})
    flask_client.post("/api/agent-messages/read-all", json={})
    flask_client.post(f"/api/agent-messages/{message['id']}/ack")
    flask_client.post(f"/api/agent-messages/{message['id']}/dismiss")
    reasons = {p.get("reason") for p in _events(q, "resolved")}
    assert reasons and reasons <= known, f"unknown retire reason: {reasons - known}"


def test_resolving_a_keyed_card_notifies_with_its_key(tmp_db, notified):
    _seed(msg_key="permission-pending")
    notified.clear()
    store.dismiss_keyed("sess-a", "permission-pending")
    assert len(notified) == 1
    _port, body = notified[0]
    assert body["resolved"]["trace_id"] == "sess-a"
    assert body["resolved"]["msg_key"] == "permission-pending"
    # The prompt is genuinely gone, so this one MAY retire a blocker banner.
    assert body["resolved"]["reason"] == "dismissed"


def test_recording_notifies_with_the_message_id(tmp_db, notified):
    message = _seed()
    assert notified[-1][1] == {"message_id": message["id"]}


def test_a_resolved_frame_reaches_open_streams(tmp_db, anon_client):
    q, _ = hub.subscribe()
    anon_client.post("/api/internal/notify",
                     json={"resolved": {"trace_id": "sess-a",
                                        "msg_key": "permission-pending"}})
    assert _events(q, "resolved") == [
        {"trace_id": "sess-a", "msg_key": "permission-pending"}]


def test_counts_stay_the_unnamed_event(tmp_db):
    from web.blueprints import notifications
    assert notifications._encode(hub.COUNTS_EVENT, {"a": 1}) == 'data: {"a": 1}\n\n'
    assert notifications._encode("notification", {"a": 1}) == \
        'event: notification\ndata: {"a": 1}\n\n'


def test_broadcast_counts_refuses_to_masquerade_as_an_event(tmp_db):
    with pytest.raises(ValueError):
        hub.broadcast_event(hub.COUNTS_EVENT, {})


def test_overflow_drops_the_head_and_never_reorders(tmp_db):
    """Overflow must not reshuffle: the consumer drains this queue with no
    lock, so a producer that drained-and-refilled to pick a victim could put a
    `notification` on the wire after the `resolved` that answers it."""
    q, _ = hub.subscribe()
    for i in range(hub._QUEUE_DEPTH + 4):
        hub.broadcast_event("notification", {"id": i})
    ids = [payload["id"] for payload in _events(q, "notification")]
    assert ids == sorted(ids), f"frames reordered: {ids}"
    assert ids[-1] == hub._QUEUE_DEPTH + 3, "the newest frame was the one lost"


def test_a_new_frame_always_wins_a_full_queue(tmp_db):
    q, _ = hub.subscribe()
    for _ in range(hub._QUEUE_DEPTH + 4):
        hub.broadcast_counts()
    hub.broadcast_event("notification", {"id": 7})
    assert _events(q, "notification") == [{"id": 7}]


def test_the_badge_severity_is_the_most_severe_unread(tmp_db):
    _seed(msg_type="progress")
    _seed(msg_type="blocker")
    _seed(msg_type="warning")
    assert hub.current_counts()["inbox_severity"] == "blocker"


def test_the_badge_severity_clears_with_the_inbox(tmp_db, flask_client):
    _seed(msg_type="blocker")
    flask_client.post("/api/agent-messages/read-all", json={})
    assert hub.current_counts()["inbox_severity"] is None


def test_the_badge_severity_ignores_test_rows(tmp_db):
    _seed(msg_type="note")
    _seed(msg_type="blocker", is_test=True)
    assert hub.current_counts()["inbox_severity"] == "note"
