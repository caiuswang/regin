"""A run stalled on a failing network (`runner._RetryLatch`).

The CLI announces every failed API call it is about to retry as a
`system`/`api_retry` frame, and announces the recovery by simply speaking
again. Before this, the frame reached `_route` and fell out of
`from_sdk_message` unhandled, so a turn stalled on a dead network was
indistinguishable from a long tool call: both emit nothing, `turn_in_flight`
stays true, and the card read `working` for as long as the outage lasted.

These tests pin both edges of the latch, the fields it hands the card — the
same ones a terminal session's status-line banner fills, so the retry strip
needs no second code path — and the scoping that keeps an abandoned turn from
claiming to reconnect forever.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import runner as runner_mod

_TRACE = "sdk-retry-1"


def _assistant():
    """The real SDK class, because the latch keys on the type NAME — the same
    discriminator `_admit` uses. A stub named anything else would pass a test
    the CLI's own frames fail."""
    from claude_agent_sdk import AssistantMessage
    return AssistantMessage(content=[], model="opus")


def _tool_result():
    from claude_agent_sdk import UserMessage
    return UserMessage(content=[])


class _System:
    """A parsed `SystemMessage`, in the shape the latch reads it."""

    def __init__(self, subtype, **data):
        self.subtype = subtype
        self.data = {"type": "system", "subtype": subtype, **data}


def _frame(**overrides):
    data = {"attempt": 2, "max_retries": 10, "retry_delay_ms": 8000,
            "error_status": None, "error": "Connection error"}
    data.update(overrides)
    return _System("api_retry", **data)


def _latch(*messages):
    latch = runner_mod._RetryLatch()
    for message in messages:
        latch.observe(message)
    return latch


# ── the frame the CLI sends ──────────────────────────────────────

def test_the_sdk_parses_the_frame_regin_reads():
    """Not regin's own fixture talking to itself: the CLI emits this shape on
    the stream-json path and the SDK is what hands it over, so the contract
    under test is the SDK's parse of it."""
    parse_message = pytest.importorskip(
        "claude_agent_sdk._internal.message_parser").parse_message

    message = parse_message({
        "type": "system", "subtype": "api_retry", "attempt": 2,
        "max_retries": 10, "retry_delay_ms": 8000, "error_status": None,
        "error": "Connection error", "session_id": "s1", "uuid": "u1",
    })

    assert type(message).__name__ == "SystemMessage"
    assert _latch(message).state.attempt == 2


# ── the latch ────────────────────────────────────────────────────

def test_a_retry_frame_raises_the_latch():
    assert _latch(_frame()).state is not None


def test_the_next_word_from_the_api_clears_it():
    """The recovery edge is an ordinary frame, not a timer: the CLI proves the
    network is back by speaking."""
    assert _latch(_frame(), _assistant()).state is None


def test_a_tool_result_does_not_clear_it():
    """A `UserMessage` is produced locally — the tool ran on this machine and
    its result travels the stream whether or not the API is reachable. Reading
    one as recovery would report a healthy session in the window between the
    failed request and the attempt that has not happened yet."""
    assert _latch(_frame(), _tool_result()).state is not None


def test_a_later_attempt_replaces_the_earlier_one():
    """The CLI re-emits per attempt with a rising count, so the newest frame
    is the whole story."""
    latch = _latch(_frame(attempt=2), _frame(attempt=7, max_retries=10))

    assert latch.state.attempt == 7


# ── what the card is handed ──────────────────────────────────────

def test_the_fields_are_the_ones_a_terminal_banner_fills():
    state = _latch(_frame()).state

    assert (state.label, state.wait) == ("Connection error", "8s")
    assert (state.attempt, state.max_retries) == (2, 10)


def test_a_no_response_frame_carries_its_own_wait():
    """The branch a degraded network renders. Its wait lives under
    `no_response.retry_wait_ms`; the top-level delay is left at the base
    backoff, so reading that one would understate the stall."""
    state = _latch(_frame(no_response={"waited_ms": 480000,
                                       "retry_wait_ms": 90000})).state

    assert state.no_response is True
    assert state.wait == "1m 30s"


def test_a_frame_with_no_message_still_names_the_error():
    """The CLI's banner falls back to the bare status when the body carried no
    message; a label-less retry strip would say nothing at all."""
    state = _latch(_frame(error="", error_status=529)).state

    assert state.label == "API error 529"


# ── what the runner reports ──────────────────────────────────────

def test_the_runner_reports_the_retry_only_while_a_turn_is_open():
    """An interrupt the CLI honours silently leaves a turn nobody ends, so the
    latch is never cleared by a result. Unscoped, that run would claim to be
    reconnecting for the rest of its life."""
    run = runner_mod.AgentRunner(_TRACE)
    run._retries.observe(_frame())

    assert run.retrying is None

    run._open_turn()
    assert run.retrying is not None


def test_the_live_screen_carries_the_banner_fields():
    run = runner_mod.AgentRunner(_TRACE)
    run._open_turn()
    run._retries.observe(_frame())

    screen = run.live_state()

    assert screen.busy is True
    assert screen.retry_label == "Connection error"
    assert screen.retry_in == "8s"
    assert (screen.retry_attempt, screen.retry_max) == (2, 10)
    # `retry_final` means "the screen named no attempt number". This frame
    # always does, so inventing one here would fabricate what the CLI said.
    assert screen.retry_final is None


def test_the_reader_feeds_the_latch():
    """The wiring, not the latch: this frame produces no spans, so before this
    it fell out of `from_sdk_message` and `_route` recorded nothing at all."""
    run = runner_mod.AgentRunner(_TRACE)
    run._open_turn()

    asyncio.run(run._route(_frame()))

    assert run.retrying is not None
    assert run.live_state().retry_attempt == 2


def test_a_healthy_turn_carries_no_retry_fields():
    run = runner_mod.AgentRunner(_TRACE)
    run._open_turn()

    screen = run.live_state()

    assert screen.retry_label is None
    assert screen.retry_attempt is None
