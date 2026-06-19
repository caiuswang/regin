"""Unit tests for lib.trace.rewind_detect.

Exercises the pure fork-detection over synthetic parentUuid graphs: a real
rewind, the false-positive shapes that the naive "2+ children" test trips on
(tool_result / attachment siblings), a plain interrupt-then-continue, and two
stacked rewinds.
"""

from __future__ import annotations

from lib.trace.rewind_detect import detect_rewinds, orphan_turn_uuids


def _ts(uuids):
    # Deterministic, monotonically increasing ISO stamps keyed by order.
    return {u: f"2026-06-13T17:{i:02d}:00.000Z" for i, u in enumerate(uuids)}


def test_single_rewind_one_fork():
    # P0(prompt) -> A0(assistant) -> {abandoned prompt P1.., live prompt P2}
    entry_parent = {
        "p0": None,
        "a0": "p0",
        # abandoned branch
        "p1": "a0",
        "a1": "p1",
        # live branch
        "p2": "a0",
        "a2": "p2",
    }
    entry_kind = {"a0": "assistant", "a1": "assistant", "a2": "assistant"}
    real = {"p0", "p1", "p2"}
    forks = detect_rewinds(
        entry_parent, entry_kind, real, ["a2"], entry_ts=_ts(entry_parent),
    )
    assert len(forks) == 1
    f = forks[0]
    assert f.fork_uuid == "a0"
    assert f.orphan_root == "p1"
    assert f.orphan_uuids == frozenset({"p1", "a1"})
    assert f.abandoned_prompt_uuids == ("p1",)
    assert f.live_child_uuid == "p2"
    assert f.span_id == "rewind-p1"


def test_tool_result_sibling_is_not_a_rewind():
    # A0 issues a tool_use; the tool_result user entry T shares A0 as parent
    # alongside the live continuation A1. T has no real prompt below it.
    entry_parent = {
        "p0": None,
        "a0": "p0",
        "t": "a0",   # tool_result user entry (NOT a real prompt)
        "a1": "a0",  # live continuation
    }
    entry_kind = {"a0": "assistant", "a1": "assistant", "t": "user"}
    real = {"p0"}
    forks = detect_rewinds(entry_parent, entry_kind, real, ["a1"])
    assert forks == []


def test_attachment_sibling_is_not_a_rewind():
    entry_parent = {
        "p0": None,
        "a0": "p0",
        "att": "a0",   # attachment row
        "a1": "a0",
    }
    entry_kind = {"a0": "assistant", "a1": "assistant"}
    forks = detect_rewinds(entry_parent, entry_kind, {"p0"}, ["a1"])
    assert forks == []


def test_interrupt_then_continue_same_branch_is_not_a_rewind():
    # An interrupt entry stays ON the live chain (single child path) — no fork.
    entry_parent = {
        "p0": None,
        "a0": "p0",
        "int": "a0",   # [Request interrupted by user] — not a real prompt
        "p1": "int",   # user continues on the same branch
        "a1": "p1",
    }
    entry_kind = {"a0": "assistant", "a1": "assistant", "int": "user"}
    forks = detect_rewinds(entry_parent, entry_kind, {"p0", "p1"}, ["a1"])
    assert forks == []


def test_prompt_edited_before_any_response_is_not_a_rewind():
    # User submits P1, then edits/resubmits it as P2 before any response.
    # Both share parent A0; the abandoned branch carries a real prompt but
    # NO assistant turn — nothing was discarded, so it's not a rewind.
    # (Real shape from session f0518744: "ejected" -> "injected" typo fix.)
    entry_parent = {
        "p0": None,
        "a0": "p0",
        "p1": "a0",   # abandoned prompt (bare, no response under it)
        "p2": "a0",   # live, edited prompt
        "a2": "p2",
    }
    entry_kind = {"a0": "assistant", "a2": "assistant", "p1": "user"}
    real = {"p0", "p1", "p2"}
    forks = detect_rewinds(
        entry_parent, entry_kind, real, ["a2"], entry_ts=_ts(entry_parent),
    )
    assert forks == []


def test_two_stacked_rewinds_to_same_node():
    # User rewinds to a0 twice: two abandoned branches share fork_uuid a0,
    # each gets its own marker (keyed on the unique orphan_root).
    entry_parent = {
        "p0": None,
        "a0": "p0",
        "p1": "a0", "a1": "p1",   # first abandoned
        "p2": "a0", "a2": "p2",   # second abandoned
        "p3": "a0", "a3": "p3",   # live
    }
    entry_kind = {k: "assistant" for k in ("a0", "a1", "a2", "a3")}
    real = {"p0", "p1", "p2", "p3"}
    forks = detect_rewinds(
        entry_parent, entry_kind, real, ["a3"], entry_ts=_ts(entry_parent),
    )
    assert len(forks) == 2
    roots = {f.orphan_root for f in forks}
    assert roots == {"p1", "p2"}
    assert all(f.fork_uuid == "a0" for f in forks)
    assert len({f.span_id for f in forks}) == 2  # distinct markers


def test_no_seeds_returns_empty():
    entry_parent = {"p0": None, "a0": "p0", "p1": "a0", "p2": "a0"}
    assert detect_rewinds(entry_parent, {}, {"p0", "p1", "p2"}, []) == []
    assert detect_rewinds(entry_parent, {}, {"p0", "p1", "p2"}, [None]) == []


def test_orphan_turn_uuids_scopes_to_assistant():
    entry_parent = {
        "p0": None, "a0": "p0",
        "p1": "a0", "a1": "p1", "tr": "a1",  # abandoned: a1 assistant, tr user
        "p2": "a0", "a2": "p2",
    }
    entry_kind = {
        "a0": "assistant", "a1": "assistant", "a2": "assistant",
        "tr": "user",
    }
    forks = detect_rewinds(entry_parent, entry_kind, {"p0", "p1", "p2"}, ["a2"])
    assert orphan_turn_uuids(forks, entry_kind) == {"a1"}


def test_cycle_guard_does_not_loop():
    # Malformed back-edge: a1 -> a0 -> a1. Walk must terminate.
    entry_parent = {"a0": "a1", "a1": "a0", "p": "a0"}
    forks = detect_rewinds(entry_parent, {"a0": "assistant"}, {"p"}, ["a1"])
    assert isinstance(forks, list)
