"""The `<followup>` block — unresolved comments reaching the next run.

The gap this closes: a task's comments had two consumers, both human-to-human,
so "I only realised after it ran that I also wanted X" had no route into the
work except overwriting the body (which destroys the original ask) or opening
a second task.

The claim under test is that an *unresolved* thread is carried into every
subsequent run until somebody resolves it — delivery does not consume it, and
`resolution_state` is the only switch. No second axis, no new table, no new UI.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.comments import store as comments
from lib.orm.models.comments import COMMENT_BODY_MAX
from lib.orm.models.tasks import RUN_STATUS_DONE, TASK_STATUS_DONE
from lib.tasks import runs


@pytest.fixture
def launcher(monkeypatch):
    """`launch_run`, minus the agent — the prompt is read back off the call."""
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _armed(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task"]["id"]
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    return task_id


def _launch(client, launcher, task_id) -> tuple[str, int]:
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[-1]["prompt"], resp.get_json()["run"]["id"]


def _comment(task_id, body, *, author_kind="user", resolved=False):
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body=body, author_kind=author_kind)
    if resolved:
        comments.resolve(thread["id"], "resolved")
    return thread["id"]


def _block(prompt: str) -> str:
    assert "<followup>" in prompt, prompt
    return prompt.split("<followup>", 1)[1].split("</followup>", 1)[0]


# 1 ── an open thread reaches the prompt

def test_an_open_thread_reaches_the_next_runs_prompt(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "also make it idempotent")

    block = _block(_launch(flask_client, launcher, task_id)[0])

    assert "also make it idempotent" in block
    assert "user" in block


# 2 ── resolving it takes it back out

def test_a_resolved_thread_is_not_delivered(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "never mind this one", resolved=True)

    prompt = _launch(flask_client, launcher, task_id)[0]

    assert "<followup>" not in prompt
    assert "never mind this one" not in prompt


def test_only_the_resolved_thread_drops_out(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "dealt with already", resolved=True)
    _comment(task_id, "still standing")

    block = _block(_launch(flask_client, launcher, task_id)[0])

    assert "still standing" in block
    assert "dealt with already" not in block


# 3 ── no open thread: the prompt is byte-for-byte what it was

def test_a_task_with_no_comments_has_the_prompt_it_had_before(
        flask_client, launcher):
    """The block is absent, not empty — the same shape `_tracker_block` has.

    Pinned as a literal rather than recomposed from the helpers, because a
    prompt assembled from the code under test would agree with any change it
    made. A task under no feature gains no `<feature>` block either: it is one
    more thing that is absent rather than empty.
    """
    task_id = _armed(flask_client, "shelf", body="wire the thing")

    prompt = _launch(flask_client, launcher, task_id)[0]

    assert prompt == (
        "wire the thing"
        "\n\n<finish>\n"
        "This work was launched from a regin task. Running the command below "
        "is the ONLY thing that marks the task complete and releases whatever "
        "depends on it; your session stays open either way, so run it as soon "
        "as the work is done rather than waiting to be stopped.\n"
        "Before you run it: merge this run's work back into the branch this "
        "run was based on — that merge is a precondition of reporting "
        "finished, not an optional step, because whatever depends on this "
        "task is released onto that branch and sees only what has landed "
        "there.\n"
        f"   {runs._finish_command(1)} --reason "
        "'<what you did, and why>'\n"
        "The reason is not bookkeeping: regin posts it on the task as your "
        "report, and it is the only account of this run anybody gets without "
        "reading the whole session. Say what you actually did, what you "
        "decided, and anything you deliberately left — a few sentences, not a "
        "transcript.\n"
        "If you could not finish, run the same command with "
        "`--status failed --reason '<what is blocking it>'`. That report stays "
        "an open thread and is delivered to the next attempt, so write it for "
        "whoever picks this up.\n"
        "</finish>"
    )


def test_an_open_thread_with_no_comments_delivers_nothing(flask_client,
                                                          launcher):
    """A thread is a container; an empty one has nothing to say."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    comments.open_thread("task", str(task_id))

    assert "<followup>" not in _launch(flask_client, launcher, task_id)[0]


# 4 ── delivery does not consume it

def test_the_same_open_thread_is_delivered_to_two_consecutive_runs(
        flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "and add a test for it")

    first, run_id = _launch(flask_client, launcher, task_id)
    runs.complete_run(run_id, status=RUN_STATUS_DONE,
                      task_status=TASK_STATUS_DONE)
    rearm = flask_client.post(f"/api/tasks/{task_id}/arm", json={})
    assert rearm.status_code == 200, rearm.get_json()
    second, _ = _launch(flask_client, launcher, task_id)

    assert "and add a test for it" in _block(first)
    assert "and add a test for it" in _block(second)


# 5 ── the bound

def test_the_block_keeps_the_newest_and_declares_what_it_dropped(
        flask_client, launcher):
    """Later instructions correct earlier ones, so the oldest go first — and
    the agent is told the history is incomplete rather than left to assume it
    has all of it."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    for index in range(6):
        _comment(task_id, f"note-{index} " + "x" * 2000)

    block = _block(_launch(flask_client, launcher, task_id)[0])

    assert "note-5" in block
    assert "note-0" not in block
    assert len(block) < runs.FOLLOWUP_MAX + 1000
    assert "2 earlier comments" in block or "omitted" in block


def test_a_single_oversized_comment_is_still_delivered(flask_client, launcher):
    """A maximal comment renders longer than the budget once it carries its
    author label. A bound that can deliver nothing is worse than one that
    overshoots by the newest comment, so the newest is never the one dropped."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "huge " + "y" * (COMMENT_BODY_MAX - 5))

    assert "huge " in _block(_launch(flask_client, launcher, task_id)[0])


# 6 ── scope: this task's threads, not its parent's

def test_a_sibling_tasks_comments_do_not_reach_this_run(flask_client,
                                                        launcher):
    """A run is one concrete piece of work, and carrying the whole feature's
    discussion into every round is how a prompt rots. Only this task's own
    threads travel."""
    from lib.features import store as feature_store

    feature = feature_store.create("the feature")
    sibling = _armed(flask_client, "another step", body="build that")
    mine = _armed(flask_client, "a step", body="wire the thing")
    for task_id in (sibling, mine):
        feature_store.adopt_task(feature.id, task_id)
    _comment(sibling, "feature-wide discussion")
    _comment(mine, "this step only")

    block = _block(_launch(flask_client, launcher, mine)[0])

    assert "this step only" in block
    assert "feature-wide discussion" not in block


# 7 ── where it sits

def test_the_followup_block_sits_between_tracker_and_feature(flask_client,
                                                             launcher):
    from lib.features import store as feature_store

    feature = feature_store.create("the feature")
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    feature_store.adopt_task(feature.id, task_id)
    _comment(task_id, "one more thing")

    prompt = _launch(flask_client, launcher, task_id)[0]

    assert prompt.index("<followup>") < prompt.index("<feature>")
    assert prompt.index("<followup>") < prompt.index("<finish>")


def test_the_followup_block_follows_the_tracker_block(flask_client, launcher):
    """`<tracker>` declares the external issue is the source of truth; the
    local notes arrive after that as "and here is what was said here"."""
    from lib.features import store as feature_store

    feature = feature_store.create("the feature", origin="linear",
                                   external_ref="CAI-1",
                                   external_url="https://x/CAI-1")
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    feature_store.adopt_task(feature.id, task_id)
    _comment(task_id, "one more thing")

    prompt = _launch(flask_client, launcher, task_id)[0]

    assert prompt.index("<tracker>") < prompt.index("<followup>")


def test_a_maximal_body_cannot_truncate_the_followup_block(flask_client,
                                                           launcher):
    task_id = _armed(flask_client, "big", body="x" * runs.PROMPT_MAX)
    _comment(task_id, "the late correction")

    assert "the late correction" in _block(
        _launch(flask_client, launcher, task_id)[0])


# ── ordering and labelling inside the block ──────────────────

def test_comments_arrive_oldest_first(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="first thing")
    comments.add_comment(thread["id"], body="second thing")

    block = _block(_launch(flask_client, launcher, task_id)[0])

    assert block.index("first thing") < block.index("second thing")


def test_each_comment_is_labelled_with_its_thread_and_author_kind(
        flask_client, launcher):
    """The thread id joined the label once the block started asking for a
    reply: `task comment` takes a thread id, and nothing else carries it."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="from a person",
                         author_kind="user")
    comments.add_comment(thread["id"], body="from a machine",
                         author_kind="agent")

    block = _block(_launch(flask_client, launcher, task_id)[0])

    assert f"[thread {thread['id']} · user] from a person" in block
    assert f"[thread {thread['id']} · agent] from a machine" in block


def test_the_block_says_why_these_are_still_here(flask_client, launcher):
    """Without it the agent reads the block as history it already handled."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _comment(task_id, "one more thing")

    head = _block(_launch(flask_client, launcher, task_id)[0]).lower()

    assert "resolve" in head
    assert "not" in head
