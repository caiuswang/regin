"""Chip lifecycle on `bridge_messages` (`kind` + `state`).

The /live "queued" chip feed is no longer a time-windowed scan of the audit
log: a row's `kind` decides whether it can ever be a chip (steers only), and
`state` advances one-way on observed events — consumed / closed / dismissed —
so nothing lingers by timer and nothing audit-only ("selected …" answers,
permission decisions) ever renders as queued.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import store


pytestmark = pytest.mark.usefixtures("tmp_db")


def _row(row_id):
    rows = store.list_bridge_messages(limit=200)
    return next(r for r in rows if r["id"] == row_id)


def _state(row_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT kind, state FROM bridge_messages WHERE id = ?", (row_id,))
        return dict(cur.fetchone())
    finally:
        conn.close()


def test_record_kind_and_birth_state():
    tmux_steer = store.record_bridge_message("T-1", "/exit", "web",
                                             pending=True)
    sdk_steer = store.record_bridge_message("T-1", "steer sdk", "web")
    answer = store.record_bridge_message("T-1", "selected Yes", "web",
                                         kind="answer")
    decision = store.record_bridge_message("T-1", "allowed the pending request",
                                           "web", kind="decision")
    assert _state(tmux_steer) == {"kind": "steer", "state": "pending"}
    # An SDK-tier steer's display truth is the runner's queue, an answer's /
    # decision's is nothing at all — all born closed, never chip-eligible.
    assert _state(sdk_steer) == {"kind": "steer", "state": "closed"}
    assert _state(answer) == {"kind": "answer", "state": "closed"}
    assert _state(decision) == {"kind": "decision", "state": "closed"}


def test_list_pending_steers_serves_delivered_pending_oldest_first():
    first = store.record_bridge_message("T-2", "first", "web", pending=True)
    second = store.record_bridge_message("T-2", "second", "web", pending=True)
    undelivered = store.record_bridge_message("T-2", "refused", "web",
                                              pending=True)
    answer = store.record_bridge_message("T-2", "selected Yes", "web",
                                         kind="answer")
    store.mark_delivered(first, True, "ok")
    store.mark_delivered(second, True, "ok")
    store.mark_delivered(undelivered, False, "pane gone")
    store.mark_delivered(answer, True, "ok")
    served = store.list_pending_steers("T-2")
    assert [r["id"] for r in served] == [first, second]
    assert [r["body"] for r in served] == ["first", "second"]


def test_settle_is_one_way_and_idempotent():
    row = store.record_bridge_message("T-3", "ran", "web", pending=True)
    store.mark_delivered(row, True, "ok")
    store.settle_steers([row], "consumed")
    assert _state(row)["state"] == "consumed"
    # A settled row never re-enters the machine: a later close, a repeat
    # settle, and a dismiss are all no-ops against it.
    store.settle_steers([row], "closed")
    assert _state(row)["state"] == "consumed"
    assert store.dismiss_steer("T-3", row) is False


def test_dismiss_is_scoped_to_the_session():
    row = store.record_bridge_message("T-4", "never ran", "web", pending=True)
    store.mark_delivered(row, True, "ok")
    assert store.dismiss_steer("someone-else", row) is False
    assert store.dismiss_steer("T-4", row) is True
    assert _state(row)["state"] == "dismissed"
    assert store.list_pending_steers("T-4") == []
    # Refusal, not error, the second time — the chip was already gone.
    assert store.dismiss_steer("T-4", row) is False


def test_dismiss_route_retires_the_chip(flask_client):
    row = store.record_bridge_message("T-5", "swallowed by an overlay", "web",
                                      pending=True)
    store.mark_delivered(row, True, "ok")
    res = flask_client.delete(f"/api/agent-runs/T-5/queue/b{row}").get_json()
    assert res["removed"] is True
    assert store.list_pending_steers("T-5") == []
    stale = flask_client.delete(f"/api/agent-runs/T-5/queue/b{row}").get_json()
    assert stale["removed"] is False


# ── the retire predicate: what the transcript "already speaks for" ──
# A steer chip has no time window, so anything the predicate cannot match
# stays on screen for the session's lifetime. The tmux tier does not deliver
# the body byte-for-byte, and each rewrite below stranded real chips.

def _partition(body, seen):
    from web.blueprints.trace import sessions
    return sessions._partition_steers([{"id": 1, "body": body}], seen)


def test_a_head_truncated_delivery_still_retires_its_chip():
    # A composer left in vim NORMAL mode runs the head of the typed text as
    # motions, so the turn claude logged is a proper suffix of what was sent.
    body = "So I would recommend the base64 route for the session SDK work"
    out, settled = _partition(body, {body[1:]})

    assert out == []
    assert settled == [1]


def test_a_short_suffix_never_retires_an_unrelated_steer():
    # Below the floor a shared ending is coincidence, not a truncated echo.
    out, settled = _partition("ship it now", {"p it now"})

    assert settled == []
    assert len(out) == 1


def test_an_image_steer_retires_against_its_materialized_paths():
    # `attachments.for_tmux` substitutes an `@<path>` for every `[Image #N]`
    # marker, so the transcript turn carries paths the recorded body — which
    # keeps the bare markers, to bind the bytes to the span — never had.
    from web.blueprints.trace import sessions
    body = "[Image #1]look at this and tell me what broke [Image #2]"
    turn = ("@/data/regin/pasted/t1/aa.png [Image #1]look at this and tell me "
            "what broke @/data/regin/pasted/t1/bb.png [Image #2]")
    out, settled = _partition(body, {sessions._delivered_key(turn)})

    assert out == []
    assert settled == [1]


def test_an_operator_at_mention_survives_the_path_strip():
    # Only the bridge's own `pasted/` materializations come off. Stripping
    # every `@token` would let two different prompts retire each other.
    from web.blueprints.trace import sessions

    assert sessions._delivered_key("@src/foo.py fix it") == "@src/foo.py fix it"


def test_a_steer_typed_onto_a_draft_still_retires_its_chip():
    # A composer already holding a human's draft is never cleared, so the
    # submitted turn is that draft followed by the steer — the body is a
    # suffix of the turn rather than the other way round.
    body = "and please double-check the retire predicate while you are there"
    out, settled = _partition(body, {f"half a thought {body}"})

    assert out == []
    assert settled == [1]


def test_the_path_strip_leaves_no_double_space():
    from web.blueprints.trace import sessions
    turn = "@/data/regin/pasted/t1/aa.png [Image #1] then the rest of it"

    assert sessions._delivered_key(turn) == "[Image #1] then the rest of it"


def test_a_pasted_content_wrapped_turn_retires_its_chip():
    # Claude Code turns every bracketed paste >= 20 trimmed chars into an
    # inline block and logs THAT as the turn, so the body the bridge pasted
    # is nowhere in the transcript verbatim (trace 58267d14).
    from web.blueprints.trace import sessions
    body = ("It has nothing related to the task status. Check if we could "
            "detect the network failure retry when this session is a SDK "
            "spot session. You could reference the paseo implementation.")
    turn = f'\n\n<pasted_content id="6eed">\n{body}\n</pasted_content id="6eed">\n'
    out, settled = _partition(body, {sessions._delivered_key(turn)})

    assert out == []
    assert settled == [1]


def test_a_wrapped_block_keeps_the_text_around_it():
    # A human types a lead-in and pastes after it; only the block unwraps.
    from web.blueprints.trace import sessions
    turn = ('check this session: \n\n<pasted_content id="6eed">\n'
            '7e1f7817-6db1-4d52-bf8f-ef2f35535680\n'
            '</pasted_content id="6eed">\n')

    assert sessions._delivered_key(turn) == (
        "check this session: 7e1f7817-6db1-4d52-bf8f-ef2f35535680")


def test_a_malformed_pasted_content_tag_is_left_alone():
    # Prose that merely quotes the tag must not be rewritten: the id is four
    # lowercase hex digits and both tags must carry the same one.
    from web.blueprints.trace import sessions
    turn = '<pasted_content id="zz">\nx\n</pasted_content id="zz">'

    assert sessions._delivered_key(turn) == ' '.join(turn.split())
