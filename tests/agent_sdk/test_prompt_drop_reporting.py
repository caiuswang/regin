"""A prompt the runner accepts and then abandons has to leave an artifact.

`submit_prompt` acks `"prompt queued"` as soon as the loop takes the call, and
the composer clears the operator's draft on that ack — so from that moment the
`bridge_messages` row is the only surviving copy of the text. Every path that
abandons the prompt after it used to return silently: no log, no span, and a
row still reading `delivered / "prompt queued"`, which is why a lost prompt
could only ever be inferred from a span that was missing.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_bridge import store as bridge_store
from lib.agent_sdk import registry, runner as runner_mod

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    FakeClient, captured, fake_client,
)

_TRACE = "sdk-drop-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


def _row(text="the prompt that never ran"):
    return bridge_store.record_bridge_message(_TRACE, text, "web:test")


def _detail(row_id):
    rows = bridge_store.list_bridge_messages(session_id=_TRACE)
    return next(r["delivery_detail"] for r in rows if r["id"] == row_id)


def _delivered(row_id):
    rows = bridge_store.list_bridge_messages(session_id=_TRACE)
    return next(r["delivered"] for r in rows if r["id"] == row_id)


async def _started():
    run = runner_mod.AgentRunner(_TRACE)
    await run.start()
    return run


def test_a_prompt_dropped_after_its_span_is_reported(captured, fake_client,
                                                     tmp_db):
    """The worst of the drop paths: the span is already posted, so the trace
    shows a prompt the CLI was never given."""
    row_id = _row()
    bridge_store.mark_delivered(row_id, True, "prompt queued")

    async def scenario():
        run = await _started()
        run.enqueue("the prompt that never ran", row_id=row_id)
        queued = run._find_pending(run.pending_prompts()[0]["id"])
        # The child dies DURING the span post — the one await `_run_turn`
        # takes before the CLI is handed anything, which is why the latch is
        # read on both sides of it.
        async def _die(_event):
            run._stream_dead = True
        run._emit = _die
        await run._run_turn(queued)

    asyncio.run(scenario())

    assert _delivered(row_id) == 0
    assert _detail(row_id) == "dropped: stream_dead_after_span"


def test_the_operators_stop_reports_every_prompt_it_strands(captured,
                                                            fake_client,
                                                            tmp_db):
    first, second = _row("one"), _row("two")

    async def scenario():
        run = await _started()
        run.enqueue("one", row_id=first)
        run.enqueue("two", row_id=second)
        await run.request_stop()

    asyncio.run(scenario())

    assert _detail(first) == "dropped: stopped_by_operator"
    assert _detail(second) == "dropped: stopped_by_operator"


def test_a_drop_is_the_last_word_on_the_row(captured, fake_client, tmp_db):
    """The request thread stamps the ack while the runner may already be
    reporting the abandonment on its own loop; the drop has to survive that
    race whichever write lands second."""
    row_id = _row()

    async def scenario():
        run = await _started()
        run.enqueue("raced", row_id=row_id)
        run._drain_pending("stream_dead_before_span")

    asyncio.run(scenario())
    bridge_store.mark_delivered(row_id, True, "prompt queued")

    assert _detail(row_id) == "dropped: stream_dead_before_span"
    assert _delivered(row_id) == 0


def test_a_dropped_prompt_is_served_to_the_card(captured, fake_client, tmp_db):
    """It leaves on the operator's dismiss and on nothing else — no observed
    event can retire the only copy of text they can no longer see."""
    row_id = _row("still visible")

    async def scenario():
        run = await _started()
        run.enqueue("still visible", row_id=row_id)
        run._drain_pending("session_ended")

    asyncio.run(scenario())

    served = bridge_store.list_dropped_steers(_TRACE)
    assert [r["id"] for r in served] == [row_id]
    assert served[0]["body"] == "still visible"


def test_a_delivered_steer_is_not_served_as_dropped(captured, fake_client,
                                                    tmp_db):
    row_id = _row("ran fine")
    bridge_store.mark_delivered(row_id, True, "prompt queued")

    assert bridge_store.list_dropped_steers(_TRACE) == []


def test_a_cancelled_prompt_is_not_served_back_as_dropped(captured,
                                                          fake_client, tmp_db):
    """The operator deleting a queued prompt is not the session losing it.

    The record stays inside the queue after `cancel_pending`, so the pump only
    reaches it once the current turn ends — reporting a drop there handed the
    deleted text straight back as a chip the moment the session went idle.
    """
    row_id = _row("cancel me")
    bridge_store.mark_delivered(row_id, True, "prompt queued")

    async def scenario():
        run = await _started()
        run.enqueue("cancel me", row_id=row_id)
        target = run.pending_prompts()[0]["id"]
        assert run.cancel_pending(target) == (True, "prompt removed")
        run.close()
        await asyncio.wait_for(run.pump(), timeout=5)
        await run.stop()
        return fake_client.prompts

    assert asyncio.run(scenario()) == []
    assert bridge_store.list_dropped_steers(_TRACE) == []
    assert _detail(row_id) == "cancelled by operator"
    assert _delivered(row_id) == 0

    # A late ack must not reopen the other door: `pending` + `delivered` is
    # the delivered-steer feed, and the chip would come back through it.
    bridge_store.mark_delivered(row_id, True, "prompt queued")
    assert bridge_store.list_pending_steers(_TRACE) == []
    assert bridge_store.list_dropped_steers(_TRACE) == []


def test_the_operators_stop_still_reports_what_it_strands(captured,
                                                          fake_client, tmp_db):
    """`request_stop` cancels every queued record too — but those the operator
    never asked to lose, so they keep earning a chip."""
    row_id = _row("stranded by stop")

    async def scenario():
        run = await _started()
        run.enqueue("stranded by stop", row_id=row_id)
        await run.request_stop()

    asyncio.run(scenario())

    assert _detail(row_id) == "dropped: stopped_by_operator"
    assert [r["id"] for r in bridge_store.list_dropped_steers(_TRACE)] == [row_id]
