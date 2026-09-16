"""A run's alias group — every child session it has been traced as.

`0012` gave a run one `cli_session_id` on the assumption that a run has one
child. `/clear` breaks that: the CLI ends the child conversation and opens
another under a fresh id while the same runner keeps driving it. The single
column could not hold the second and the write refused it as a conflict, so
every durable reader saw only the first child and an heir looked like a session
regin had never launched — no cwd for its `/` catalog, invisible to the resume
picker, and never settled when the run died.

Two shapes have to coexist here, and neither side of the pair is unique:

  * one run, many children — what `/clear` makes,
  * one child, many runs — what resuming makes, because it keeps the child's
    session id (`fork_session=False`) and the previous `exited` row goes on
    naming it. `reap_orphaned_runs` depends on that: it subtracts every id a
    LIVE run names before settling a dead one's.

What the group deliberately does NOT change is `agent_runs.cli_session_id`. It
stays pinned to the first child because the trace view unions the run's
SDK-stream spans with exactly that session, and a `/clear` opens a new
conversation rather than continuing the old one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest import mock

from lib import hook_plugin
from lib.agent_sdk import runner, store
from lib.trace.alias import sdk_span_trace_id

_RUN = "sdk-group01"
_FIRST = "child-first"
_HEIR = "child-after-clear"


@dataclass
class _Message:
    """The only thing `_note_session` reads off an SDK frame."""

    session_id: str


def _link(run: str, *children: str) -> None:
    store.upsert_run(run, status="running")
    for child in children:
        store.set_cli_session(run, child)


# ── one run, many children (the `/clear` shape) ──────────────


def test_a_clear_heir_joins_the_group_and_resolves_to_its_run(tmp_db):
    """The bug in one assertion: before the group existed, the heir resolved
    to no run at all, so it had no cwd, no SDK routing and no resume entry."""
    _link(_RUN, _FIRST, _HEIR)

    assert store.find_run(_HEIR)["trace_id"] == _RUN
    assert store.sdk_trace_for_cli_session(_HEIR) == _RUN
    assert store.find_run(_FIRST)["trace_id"] == _RUN


def test_the_union_column_stays_pinned_to_the_first_child(tmp_db):
    """`agent_runs.cli_session_id` is what the trace view unions the run's
    SDK-stream spans with. Moving it to the heir would fuse two sessions a
    person deliberately separated and orphan the first half's spans."""
    _link(_RUN, _FIRST, _HEIR)

    assert store.get_run(_RUN)["cli_session_id"] == _FIRST
    assert store.cli_session_for(_RUN) == _FIRST


def test_every_child_counts_as_sdk_owned(tmp_db):
    """The dual-writer guard reads this set. An heir missing from it reads as
    hook-owned — the split this guard exists to close, on the very sessions a
    run drives last."""
    _link(_RUN, _FIRST, _HEIR)

    assert {_FIRST, _HEIR} <= store.sdk_child_session_ids()


def test_relinking_the_same_child_is_a_no_op(tmp_db):
    """The runner calls this for every message it sees."""
    _link(_RUN, _FIRST, _FIRST, _FIRST, _HEIR, _HEIR)

    assert store.sdk_child_session_ids() == {_FIRST, _HEIR}


# ── one child, many runs (the relaunch shape) ────────────────


def test_a_relaunch_may_name_a_child_an_older_dead_run_still_names(tmp_db):
    """Resuming keeps the child's session id, so the previous `exited` row goes
    on naming it. Refusing the second link is what let the settle sweep end a
    conversation the live run was driving."""
    store.upsert_run("sdk-stale", status="exited")
    store.set_cli_session("sdk-stale", _FIRST)
    store.upsert_run("sdk-live", status="running")
    store.set_cli_session("sdk-live", _FIRST)

    assert store.get_run("sdk-live")["cli_session_id"] == _FIRST
    assert store.get_run("sdk-stale")["cli_session_id"] == _FIRST


# ── still fail-closed ────────────────────────────────────────


def test_a_child_of_no_run_resolves_to_nothing(tmp_db):
    assert store.find_run("never-launched") is None
    assert store.sdk_trace_for_cli_session("never-launched") is None


def test_a_run_that_does_not_exist_records_no_child(tmp_db):
    """The link hangs off a real run row; inventing one would make a group
    member out of a session nothing launched."""
    store.set_cli_session("sdk-ghost", _FIRST)

    assert store.sdk_child_session_ids() == set()
    assert store.find_run(_FIRST) is None


def test_a_run_never_links_itself(tmp_db):
    store.upsert_run(_RUN, status="running")
    store.set_cli_session(_RUN, _RUN)

    assert store.sdk_child_session_ids() == set()


# ── the SDK stream follows the conversation, not the run ─────


def test_set_cli_session_reports_the_runs_first_child(tmp_db):
    """The runner needs to tell an heir from the original once per message, and
    a resume reuses the run's trace id — so `self._session_id` being unset says
    nothing about which child this is."""
    store.upsert_run(_RUN, status="running")

    assert store.set_cli_session(_RUN, _FIRST) == _FIRST
    assert store.set_cli_session(_RUN, _HEIR) == _FIRST
    assert store.set_cli_session(_RUN, _HEIR) == _FIRST


def test_set_cli_session_reports_nothing_for_a_run_that_does_not_exist(tmp_db):
    assert store.set_cli_session("sdk-ghost", _FIRST) is None


def test_the_stream_moves_to_a_segment_when_clear_opens_an_heir(tmp_db):
    """`/clear` is the bug's trigger: the same runner keeps posting, but what it
    says now belongs to the new conversation."""
    _link(_RUN, _FIRST)
    run = runner.AgentRunner(_RUN)

    run._note_session(_Message(_FIRST))
    assert run._span_trace_id == _RUN

    run._note_session(_Message(_HEIR))
    assert run._span_trace_id == sdk_span_trace_id(_RUN, _HEIR)


def test_a_resumed_heir_takes_its_segment_on_the_first_message(tmp_db):
    """A resume reuses the run's trace id, so its first message can name a
    child that was already an heir before this process existed."""
    _link(_RUN, _FIRST, _HEIR)
    run = runner.AgentRunner(_RUN)

    run._note_session(_Message(_HEIR))

    assert run._span_trace_id == sdk_span_trace_id(_RUN, _HEIR)


def test_posted_spans_carry_the_conversations_trace(tmp_db, monkeypatch):
    _link(_RUN, _FIRST)
    posted: list[str] = []
    monkeypatch.setattr(hook_plugin, "post_span",
                        lambda **span: posted.append(span["trace_id"]))
    run = runner.AgentRunner(_RUN)

    asyncio.run(run._post({"trace_id": _RUN, "name": "prompt"}))
    run._note_session(_Message(_HEIR))
    asyncio.run(run._post({"trace_id": _RUN, "name": "prompt"}))

    assert posted == [_RUN, sdk_span_trace_id(_RUN, _HEIR)]


def test_a_span_naming_another_trace_is_left_alone(tmp_db):
    """Only the run's own id is rewritten: a subagent span already names the
    trace it belongs to."""
    _link(_RUN, _FIRST)
    posted: list[str] = []
    run = runner.AgentRunner(_RUN)
    run._note_session(_Message(_HEIR))

    with mock.patch.object(hook_plugin, "post_span",
                           side_effect=lambda **s: posted.append(s["trace_id"])):
        asyncio.run(run._post({"trace_id": "sa-other", "name": "x"}))

    assert posted == ["sa-other"]


def test_a_resume_onto_an_heir_starts_on_its_segment(tmp_db):
    """The pump can send the resume's prompt before any message has named the
    child, so waiting for `_note_session` would drop that prompt's span into
    the conversation the `/clear` retired."""
    _link(_RUN, _FIRST, _HEIR)

    run = runner.AgentRunner(_RUN, resume=_HEIR)

    assert run._span_trace_id == sdk_span_trace_id(_RUN, _HEIR)


def test_a_resume_onto_the_first_child_keeps_the_runs_own_trace(tmp_db):
    _link(_RUN, _FIRST)

    assert runner.AgentRunner(_RUN, resume=_FIRST)._span_trace_id == _RUN
    assert runner.AgentRunner(_RUN)._span_trace_id == _RUN


def test_a_brand_new_run_starts_on_its_own_trace(tmp_db):
    """No row yet, so nothing names a first child."""
    assert runner.AgentRunner("sdk-unlaunched")._span_trace_id == "sdk-unlaunched"
