"""Frames that arrive after a turn's result (`lib/agent_sdk/runner`).

This harness runs `Agent` subagents in the **background**, so the CLI keeps
emitting long after the `ResultMessage` that ends the prompted turn, and it
holds those frames in a 100-slot stream shared with the `control_request`
frames that carry permission asks. A session that stops draining at a result
therefore wedges its whole child once that buffer fills: nothing answers
`can_use_tool` and the subagent hangs mid-tool-call.

So the stream is drained for the life of the session; a turn waits only for its
own result; the agent's own background work is a turn of its own rather than
spend billed to whichever prompt ran last; and a stream that ends takes the
session with it instead of leaving a turn waiting on a result nothing will
send.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import pytest

from lib.agent_sdk import registry, runner as runner_mod
from lib.settings import settings

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    AssistantMessage, FakeClient, ResultMessage, TextBlock, ThinkingBlock,
    captured,
)


@dataclass
class TaskStarted:
    """`system`/`task_started`. `task_type` lives in the raw payload, which the
    SDK's typed message keeps on `data` (`claude_agent_sdk.types`)."""

    task_id: str
    task_type: str = "local_agent"
    subtype: str = "task_started"
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        self.data = {"task_type": self.task_type, "task_id": self.task_id}


@dataclass
class TaskNotification:
    """`system`/`task_notification` — a task reporting its own terminal state."""

    task_id: str
    status: str = "completed"
    subtype: str = "task_notification"
    data: dict = field(default_factory=dict)


@dataclass
class TaskUpdated:
    """`system`/`task_updated`. Not every terminal task emits a notification;
    some report only here, through `patch.status`."""

    task_id: str
    patch: dict = field(default_factory=lambda: {"status": "completed"})
    subtype: str = "task_updated"
    data: dict = field(default_factory=dict)


# What the SDK's `create_memory_object_stream` holds before its writer blocks.
_BUFFER = 100


class BackgroundClient(FakeClient):
    """A CLI whose child keeps talking after the turn's result.

    Its buffer is bounded exactly as the SDK's is and `emit` is the writer that
    fills it, so a runner that stops draining at the result wedges this fake
    the same way it wedges a real `claude`. `can_use_tool` is held here for the
    same reason the SDK holds it: the loop that fills the stream is also the
    one that dispatches the control frames carrying permission asks.
    """

    def __init__(self):
        super().__init__()
        self.frames = asyncio.Queue(maxsize=_BUFFER)
        self.can_use_tool = None

    async def connect(self):
        self.connects += 1
        self.frames = asyncio.Queue(maxsize=_BUFFER)

    async def emit(self, *messages):
        for message in messages:
            await self.frames.put(message)


class ScriptedClient(BackgroundClient):
    """Answers nothing on its own: the test writes every frame, so which turn
    a result belongs to is the test's to decide rather than the fake's."""

    async def query(self, text):
        self.prompts.append(text)


def _install(monkeypatch, fake):
    def _new_client(**kwargs):
        fake.can_use_tool = kwargs.get("can_use_tool")
        return fake

    fake.rows = []
    monkeypatch.setattr(runner_mod.client, "new_client", _new_client)
    monkeypatch.setattr(runner_mod.store, "upsert_run",
                        lambda tid, **kw: fake.rows.append(kw))
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "model", "claude-opus-5")
    return fake


@pytest.fixture
def background_client(monkeypatch):
    return _install(monkeypatch, BackgroundClient())


@pytest.fixture
def scripted_client(monkeypatch):
    return _install(monkeypatch, ScriptedClient())


def _run(coro):
    return asyncio.run(coro)


def _spans(captured, name):
    return [s for s in captured["spans"] if s["name"] == name]


def _names(captured):
    return [s["name"] for s in captured["spans"]]


def _rows(captured):
    return [r for name, payload in captured["events"] if name == "turn_usage"
            for r in payload]


async def _settle(predicate, timeout: float = 5.0) -> bool:
    """Let the loop run until `predicate` holds. Returns whether it ever did."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _live(trace_id: str):
    run = runner_mod.AgentRunner(trace_id)
    await run.start()
    return run, asyncio.create_task(run.pump())


async def _shut_down(run, pump):
    run.close()
    await asyncio.wait_for(pump, timeout=5)
    await run.stop()


def _chatter(count: int):
    return [AssistantMessage(content=[TextBlock(f"bg {i}")])
            for i in range(count)]


def _subagent_chatter(count: int):
    """What a backgrounded `Agent` subagent emits: assistant frames carrying
    `parent_tool_use_id`, and never a top-level result of their own."""
    return [AssistantMessage(content=[TextBlock(f"sub {i}")],
                             parent_tool_use_id="toolu_task")
            for i in range(count)]


def _result(output_tokens: int):
    return ResultMessage(usage={"input_tokens": 1,
                                "output_tokens": output_tokens,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0})


async def _prompt_behind_background(captured, trace_id, fake, text):
    """A background turn already open, then an operator's prompt behind it.

    The overlap the CLI produces whenever a steer arrives while a subagent is
    still working — and the state in which two results are owed.
    """
    run, pump = await _live(trace_id)
    await fake.emit(AssistantMessage(content=[TextBlock("bg working")]))
    assert await _settle(lambda: _spans(captured, "assistant_response"))
    run.enqueue(text)
    assert await _settle(lambda: fake.prompts == [text])
    return run, pump


@pytest.fixture(autouse=True)
def _clean():
    yield
    for trace_id in ("sdk-flood", "sdk-control", "sdk-orphan", "sdk-woken",
                     "sdk-interleaved", "sdk-silent", "sdk-busy", "sdk-phase",
                     "sdk-phase2", "sdk-queued-stop", "sdk-tear", "sdk-dead",
                     "sdk-dead2", "sdk-stopclose", "sdk-interleaved2",
                     "sdk-attr", "sdk-stillborn", "sdk-dead3",
                     "sdk-oneshot-clean", "sdk-oneshot-dead",
                     "sdk-queued-stop2", "sdk-oneshot-wake",
                     "sdk-stop-nowait", "sdk-wake1", "sdk-wake2", "sdk-wake4",
                     "sdk-wake-shell", "sdk-wake-upd", "sdk-wake-stale",
                     "sdk-settle-inflight", "sdk-wake-running"):
        registry.unregister_run(trace_id)


# ── the freeze ─────────────────────────────────────────────────────────


def test_frames_past_the_buffer_never_block_the_child(captured,
                                                      background_client):
    """The deadlock itself: more background frames than the stream holds, all
    of them after the turn's result."""
    async def scenario():
        run, pump = await _live("sdk-flood")
        run.enqueue("go")
        assert await _settle(lambda: _rows(captured))

        await asyncio.wait_for(background_client.emit(*_chatter(150)),
                               timeout=10)
        assert await _settle(
            lambda: len(_spans(captured, "assistant_response")) == 151)
        await _shut_down(run, pump)

    _run(scenario())

    said = [s["attributes"]["text"] for s in _spans(captured,
                                                    "assistant_response")]
    assert said[0] == "answer 1"
    assert said[1:] == [f"bg {i}" for i in range(150)]


def test_a_permission_ask_after_the_result_still_gets_a_decision(
        captured, background_client):
    """The freeze as an operator sees it: a background subagent's tool call
    waiting on a `can_use_tool` the runner never reaches."""
    decisions = []

    class Ctx:
        tool_use_id = "toolu_bg"

    async def child_work():
        # The order the SDK's own loop works in: frames first, then the
        # control frame behind them.
        await background_client.emit(*_chatter(120))
        decisions.append(await background_client.can_use_tool(
            "Read", {"file_path": "/tmp/x"}, Ctx()))

    async def scenario():
        run, pump = await _live("sdk-control")
        run.enqueue("go")
        assert await _settle(lambda: _rows(captured))

        await asyncio.wait_for(child_work(), timeout=10)
        await _shut_down(run, pump)

    _run(scenario())

    assert decisions and type(decisions[0]).__name__ == "PermissionResultAllow"


# ── whose turn a frame belongs to ──────────────────────────────────────


def test_an_orphan_result_writes_no_usage_row(captured, background_client):
    """A result with no turn open belongs to no turn — billing it to the
    previous one would double-count that turn's spend."""
    async def scenario():
        run, pump = await _live("sdk-orphan")
        await background_client.emit(ResultMessage())
        assert await _settle(lambda: background_client.frames.empty())
        await _shut_down(run, pump)

    _run(scenario())

    assert _rows(captured) == []


def test_a_background_turn_gets_its_own_identity(captured, background_client):
    """Assistant frames with no prompt behind them are the agent's own turn,
    and their spans have to join the usage row that turn writes."""
    async def scenario():
        run, pump = await _live("sdk-woken")
        run.enqueue("go")
        assert await _settle(lambda: _rows(captured))

        await background_client.emit(
            AssistantMessage(content=[TextBlock("subagent reporting")]),
            ResultMessage())
        assert await _settle(lambda: len(_rows(captured)) == 2)
        await _shut_down(run, pump)

    _run(scenario())

    woken = next(s for s in _spans(captured, "assistant_response")
                 if s["attributes"]["text"] == "subagent reporting")
    assert woken["attributes"]["turn_index"] == 1
    assert woken["attributes"]["turn_uuid"] == "sdk-woken:turn-1"
    assert _rows(captured)[1]["turn_uuid"] == "sdk-woken:turn-1"


def test_background_work_between_prompts_bills_only_itself(captured,
                                                           background_client):
    """Two prompts around a piece of the agent's own work: three turns, three
    rows, and neither prompt's row moved. The two-prompt case on its own is
    `test_each_turn_posts_its_own_usage_row`."""
    async def scenario():
        run, pump = await _live("sdk-interleaved")
        run.enqueue("first")
        assert await _settle(lambda: _rows(captured))

        await background_client.emit(*_chatter(3), ResultMessage())
        assert await _settle(lambda: len(_rows(captured)) == 2)
        run.enqueue("second")
        assert await _settle(lambda: len(_rows(captured)) == 3)
        await _shut_down(run, pump)

    _run(scenario())

    assert background_client.prompts == ["first", "second"]
    assert [r["turn_uuid"] for r in _rows(captured)] == [
        "sdk-interleaved:turn-0", "sdk-interleaved:turn-1",
        "sdk-interleaved:turn-2"]


# ── the wake ledger ────────────────────────────────────────────────────
#
# A delegated agent task's completion wakes the agent for a follow-up turn of
# its own, ending in its own top-level result. Nothing on that result says so,
# and the operator's turn is usually still open when it lands — so the CLI's
# `system` task-lifecycle frames are the only thing that can tell the two
# results apart.


async def _wake_owed(fake, task_id="task_a46", terminal=None):
    """A tracked agent task that finishes while the agent is idle.

    That is what wakes it for a follow-up turn of its own; a task that settles
    while a turn is already running is folded into that turn instead.
    """
    await fake.emit(TaskStarted(task_id=task_id),
                    terminal or TaskNotification(task_id=task_id))
    assert await _settle(lambda: fake.frames.empty())


def test_a_wake_result_does_not_end_the_prompt_it_interrupts(captured,
                                                             scripted_client,
                                                             monkeypatch):
    """The core of it: the operator's turn stays open, and their next queued
    prompt does not go out on a result that was never theirs."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake1")
        await _wake_owed(scripted_client)
        run.enqueue("p2")
        assert await _settle(lambda: scripted_client.prompts)
        run.enqueue("p3")

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("subagent a46 finished")]),
            _result(11))
        assert await _settle(lambda: _rows(captured))
        mid = (list(scripted_client.prompts), run.pending_prompts())

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("the answer to p2")]),
            _result(22))
        assert await _settle(lambda: scripted_client.prompts == ["p2", "p3"])
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return mid

    sent, queued = _run(scenario())

    assert sent == ["p2"]
    assert queued == ["p3"]


def test_a_wake_gets_its_own_turn_and_its_own_usage_row(captured,
                                                        scripted_client,
                                                        monkeypatch):
    """The wake's text and spend are its own work, not the operator's."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake2")
        await _wake_owed(scripted_client)
        run.enqueue("what is 2+2?")
        assert await _settle(lambda: scripted_client.prompts)

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("subagent a46 finished")]),
            _result(11))
        await scripted_client.emit(AssistantMessage(content=[TextBlock("4")]),
                                   _result(22))
        assert await _settle(lambda: len(_rows(captured)) == 2)
        await _shut_down(run, pump)

    _run(scenario())

    said = {s["attributes"]["text"]: s["attributes"].get("turn_uuid")
            for s in _spans(captured, "assistant_response")}
    prompt = _spans(captured, "prompt")[0]["attributes"]
    # The wake is turn-1: the operator's prompt opened turn-0 and kept it.
    assert said == {"subagent a46 finished": "sdk-wake2:turn-1",
                    "4": "sdk-wake2:turn-0"}
    assert prompt.get("turn_uuid") in (None, "sdk-wake2:turn-0")
    assert [(r["turn_uuid"], r["output_tokens"]) for r in _rows(captured)] == [
        ("sdk-wake2:turn-1", 11), ("sdk-wake2:turn-0", 22)]


def test_four_prompts_around_four_wakes_keep_four_answers(captured,
                                                          scripted_client,
                                                          monkeypatch):
    """The phase shift never re-paired on its own: every question landed on one
    turn and its answer on the next, for the life of the session."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake4")
        for i in range(4):
            await _wake_owed(scripted_client, task_id=f"task_{i}")
            run.enqueue(f"q{i}")
            assert await _settle(lambda: len(scripted_client.prompts) == i + 1)
            await scripted_client.emit(
                AssistantMessage(content=[TextBlock(f"wake {i}")]), _result(1))
            await scripted_client.emit(
                AssistantMessage(content=[TextBlock(f"a{i}")]), _result(2))
            assert await _settle(lambda: len(_rows(captured)) == 2 * (i + 1))
        await _shut_down(run, pump)

    _run(scenario())

    assert scripted_client.prompts == ["q0", "q1", "q2", "q3"]
    answers = {s["attributes"]["text"]: s["attributes"]["turn_uuid"]
               for s in _spans(captured, "assistant_response")
               if s["attributes"]["text"].startswith("a")}
    # Four answers on four distinct turns, and none of them a wake's.
    assert len(set(answers.values())) == 4
    wakes = {s["attributes"]["turn_uuid"]
             for s in _spans(captured, "assistant_response")
             if s["attributes"]["text"].startswith("wake")}
    assert wakes.isdisjoint(answers.values())


def test_a_background_shell_is_never_owed_a_wake(captured, scripted_client,
                                                 monkeypatch):
    """`Bash(run_in_background=True)` travels the same frames but may never
    reach a terminal status, so owing a wake for one would hold the operator's
    turn open for the life of the session."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake-shell")
        await scripted_client.emit(TaskStarted(task_id="shell_1",
                                               task_type="local_shell"),
                                   TaskNotification(task_id="shell_1"))
        assert await _settle(lambda: scripted_client.frames.empty())
        run.enqueue("tail the log")
        assert await _settle(lambda: scripted_client.prompts)

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("started tailing")]),
            _result(7))
        assert await _settle(lambda: _rows(captured))
        await _shut_down(run, pump)

    _run(scenario())

    # One turn, the operator's: the shell never opened one of its own.
    assert [r["turn_uuid"] for r in _rows(captured)] == ["sdk-wake-shell:turn-0"]


def test_a_progress_update_leaves_a_running_task_tracked(captured,
                                                         scripted_client,
                                                         monkeypatch):
    """`task_updated` reports every state change, not only the last one.

    Retiring a task on a `running` patch costs twice over: the teardown gate
    goes back to trusting a lull while the agent still has work that can speak,
    and the terminal frame that does arrive finds nothing to clear — so the
    wake it should have owed is lost and its result ends the operator's turn.
    """
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake-running")
        await scripted_client.emit(TaskStarted(task_id="task_r"),
                                   TaskUpdated(task_id="task_r",
                                               patch={"status": "running"}))
        assert await _settle(lambda: scripted_client.frames.empty())
        mid_flight = run._wakes.in_flight

        await scripted_client.emit(TaskNotification(task_id="task_r"))
        assert await _settle(lambda: scripted_client.frames.empty())
        run.enqueue("p1")
        assert await _settle(lambda: scripted_client.prompts)
        run.enqueue("p2")

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("task_r finished")]),
            _result(11))
        assert await _settle(lambda: _rows(captured))
        # A beat for a pump this result had wrongly released to send p2.
        await asyncio.sleep(0.05)
        sent = list(scripted_client.prompts)

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("p1 answered")]), _result(22))
        assert await _settle(lambda: scripted_client.prompts == ["p1", "p2"])
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return mid_flight, sent

    in_flight, sent = _run(scenario())

    assert in_flight is True
    # The wake was still owed, so its result did not end the operator's turn.
    assert sent == ["p1"]
    assert [r["turn_uuid"] for r in _rows(captured)] == [
        "sdk-wake-running:turn-1", "sdk-wake-running:turn-0"]


def test_a_terminal_task_update_alone_still_owes_the_wake(captured,
                                                          scripted_client,
                                                          monkeypatch):
    """Not every terminal task emits a notification — some report only through
    a `task_updated` patch."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake-upd")
        await _wake_owed(scripted_client, task_id="task_u",
                         terminal=TaskUpdated(task_id="task_u",
                                              patch={"status": "completed"}))
        run.enqueue("p1")
        assert await _settle(lambda: scripted_client.prompts)

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("task_u finished")]),
            _result(11))
        assert await _settle(lambda: _rows(captured))
        still_open = run._open and run._open[0].done is not None
        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("p1 answered")]), _result(22))
        assert await _settle(lambda: len(_rows(captured)) == 2)
        await _shut_down(run, pump)
        return still_open

    assert _run(scenario()) is True
    assert [r["turn_uuid"] for r in _rows(captured)] == [
        "sdk-wake-upd:turn-1", "sdk-wake-upd:turn-0"]


def test_a_completion_inside_a_running_turn_is_owed_nothing(captured,
                                                            scripted_client,
                                                            monkeypatch):
    """The guard on the ledger: a task that settles while a turn is already
    running is reported inside that turn, not on one of its own.

    Owing a wake for it would be the same early release from the other side —
    the debt would consume the operator's own result, leaving their turn open
    on an answer that had already arrived and their next prompt queued behind
    it for the life of the session.
    """
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0)

    async def scenario():
        run, pump = await _live("sdk-wake-stale")
        run.enqueue("p1")
        assert await _settle(lambda: scripted_client.prompts)
        run.enqueue("p2")

        await scripted_client.emit(TaskStarted(task_id="task_mid"),
                                   TaskNotification(task_id="task_mid"))
        # No turn of its own follows: only p1's own answer and result.
        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("p1 answered")]), _result(22))
        assert await _settle(lambda: scripted_client.prompts == ["p1", "p2"])
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()

    _run(scenario())

    said = _spans(captured, "assistant_response")[0]["attributes"]
    assert said["turn_uuid"] == "sdk-wake-stale:turn-0"
    assert [r["turn_uuid"] for r in _rows(captured)] == ["sdk-wake-stale:turn-0"]


# ── whose turn a subagent's frames are ─────────────────────────────────


def test_two_prompts_still_bill_exactly_two_turns(captured, background_client):
    """A subagent working across both prompts is not a turn.

    The CLI owes a top-level result only to the main agent, so a turn opened
    for a subagent's frames can never be closed — and the pump waiting on it is
    bounded by nothing: `idle_timeout_sec` covers only the gap *between* turns.
    The `wait_for` in `_shut_down` is what makes that hang a failure here.
    """
    async def scenario():
        run, pump = await _live("sdk-interleaved2")
        run.enqueue("first")
        assert await _settle(lambda: _rows(captured))

        await background_client.emit(*_subagent_chatter(3))
        assert await _settle(
            lambda: len(_spans(captured, "assistant_response")) == 4)
        run.enqueue("second")
        assert await _settle(lambda: len(_rows(captured)) == 2)
        await _shut_down(run, pump)

    _run(scenario())

    rows = _rows(captured)
    assert background_client.prompts == ["first", "second"]
    assert len(rows) == 2
    assert rows[0]["turn_uuid"] != rows[1]["turn_uuid"]


def test_a_subagents_frames_do_not_own_the_operators_answer(captured,
                                                            scripted_client):
    """A subagent reporting in before the operator speaks must not take the
    turn: the question and its answer would be split across two identities and
    the answer billed to the subagent's row."""
    async def scenario():
        run, pump = await _live("sdk-attr")
        await scripted_client.emit(*_subagent_chatter(1))
        assert await _settle(lambda: _spans(captured, "assistant_response"))

        run.enqueue("what is 2+2?")
        assert await _settle(lambda: scripted_client.prompts)
        await scripted_client.emit(AssistantMessage(content=[TextBlock("4")]),
                                   _result(7))
        assert await _settle(lambda: _rows(captured))
        await _shut_down(run, pump)

    _run(scenario())

    said = {s["attributes"]["text"]: s["attributes"].get("turn_uuid")
            for s in _spans(captured, "assistant_response")}
    # The prompt is turn-0: the subagent's frame allocated no index at all.
    assert said == {"sub 0": None, "4": "sdk-attr:turn-0"}
    assert [r["turn_uuid"] for r in _rows(captured)] == ["sdk-attr:turn-0"]


# ── two turns at once ──────────────────────────────────────────────────


def test_a_background_result_does_not_release_the_prompted_turn(
        captured, scripted_client, monkeypatch):
    """One prompt at a time is the pump's whole contract. A result owed to the
    background turn must not stand in for the operator's: the next queued
    prompt would go out while this one was still running, and the session would
    stay one result out of phase for the rest of its life."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)

    async def scenario():
        run, pump = await _prompt_behind_background(
            captured, "sdk-phase", scripted_client, "p2")
        run.enqueue("p3")

        await scripted_client.emit(_result(11))
        assert await _settle(lambda: len(_rows(captured)) == 1)
        mid = (list(scripted_client.prompts), run.pending_prompts())

        await scripted_client.emit(_result(22))
        assert await _settle(lambda: scripted_client.prompts == ["p2", "p3"])
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return mid

    sent, queued = _run(scenario())

    assert sent == ["p2"]
    # And `/live` still reports the steer as waiting, because it is.
    assert queued == ["p3"]


def test_each_turn_bills_the_result_that_was_owed_to_it(captured,
                                                        scripted_client,
                                                        monkeypatch):
    """The background turn is billed the first result and the prompt's turn the
    second, each on its own index — the order they were opened in."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)

    async def scenario():
        run, pump = await _prompt_behind_background(
            captured, "sdk-phase2", scripted_client, "p2")
        await scripted_client.emit(_result(11), _result(22))
        assert await _settle(lambda: len(_rows(captured)) == 2)
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()

    _run(scenario())

    rows = _rows(captured)
    assert [(r["turn_uuid"], r["output_tokens"]) for r in rows] == [
        ("sdk-phase2:turn-0", 11), ("sdk-phase2:turn-1", 22)]


def test_a_queued_stop_cannot_disconnect_a_turn_that_is_still_running(
        captured, scripted_client):
    """An operator's Stop queued behind a steer — and the `one_shot`
    terminator, which is the same queue entry. Ending the pump on the
    background turn's result would disconnect the child mid-turn and destroy
    the steer, leaving a trace whose last two rows are `prompt` and
    `session.end`."""
    async def scenario():
        run, pump = await _prompt_behind_background(
            captured, "sdk-queued-stop", scripted_client, "p2")
        run.close()

        await scripted_client.emit(_result(11))
        assert await _settle(lambda: len(_rows(captured)) == 1)
        mid = (pump.done(), scripted_client.disconnects)

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("p2 answer")]), _result(22))
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return mid

    pump_done, disconnects = _run(scenario())

    assert pump_done is False
    assert disconnects == 0
    answered = next(s for s in _spans(captured, "assistant_response")
                    if s["attributes"]["text"] == "p2 answer")
    assert answered["attributes"]["turn_uuid"] == "sdk-queued-stop:turn-1"
    assert _names(captured)[-1] == "session.end"


def test_teardown_waits_out_a_task_that_is_still_running(captured,
                                                         scripted_client,
                                                         monkeypatch):
    """A lull is not proof the child has finished talking.

    Measured against a live child, the gap from a task settling to the wake
    turn's first frame was 6.4s, so a quarter-second of quiet says nothing
    while delegated work is still running. Until it reports terminal the wait
    runs to the `stop_grace_sec` deadline instead of ending on the first lull.
    """
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 0.1)
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 3)

    async def scenario():
        run, pump = await _live("sdk-settle-inflight")
        await scripted_client.emit(TaskStarted(task_id="task_slow"))
        run.enqueue("summarise the repo")
        assert await _settle(lambda: scripted_client.prompts)
        run.close()
        await scripted_client.emit(_result(9))
        await asyncio.wait_for(pump, timeout=5)

        stopping = asyncio.create_task(run.stop())
        # Longer than the lull, shorter than the grace: the gap a real wake
        # leaves between its task settling and its first frame.
        await asyncio.sleep(0.6)
        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("the wake's answer")]),
            _result(22), TaskNotification(task_id="task_slow"))
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(stopping, timeout=10)
        return asyncio.get_running_loop().time() - started

    left_to_wait = _run(scenario())

    assert "the wake's answer" in [s["attributes"]["text"]
                                   for s in _spans(captured,
                                                   "assistant_response")]
    assert _names(captured)[-1] == "session.end"
    # The task reported terminal with the answer, so the next lull ended the
    # wait rather than running out the whole grace period.
    assert left_to_wait < 2


def test_a_queued_stop_waits_for_an_answer_still_in_flight(captured,
                                                           scripted_client,
                                                           monkeypatch):
    """The same terminator, prompt first and wake second — the ordering
    `_prompt_behind_background` cannot produce.

    Here the operator's turn is the only slot open, so the wake's result ends
    it and releases the pump while the answer is still being written. Nothing
    on a result says which turn it ended, so teardown has to hold the transport
    open rather than take the release as proof of an answer.
    """
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 1)

    async def scenario():
        run, pump = await _live("sdk-queued-stop2")
        run.enqueue("summarise the repo")
        assert await _settle(lambda: scripted_client.prompts)
        run.close()

        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("subagent a46 finished")]),
            _result(9))
        await asyncio.wait_for(pump, timeout=5)
        assert scripted_client.disconnects == 0

        stopping = asyncio.create_task(run.stop())
        await asyncio.sleep(0.05)
        await scripted_client.emit(
            AssistantMessage(content=[TextBlock("the summary you asked for")]),
            _result(22))
        await asyncio.wait_for(stopping, timeout=10)

    _run(scenario())

    assert [s["attributes"]["text"]
            for s in _spans(captured, "assistant_response")] == [
        "subagent a46 finished", "the summary you asked for"]
    assert _names(captured)[-1] == "session.end"
    assert scripted_client.disconnects == 1


def test_an_operator_stop_does_not_wait_for_the_stream(captured,
                                                       scripted_client,
                                                       monkeypatch):
    """Someone who pressed Stop asked for the session to end, not for one more
    answer — so a child still chattering must not hold the teardown open for
    the length of the settle window."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 10)
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 10)

    async def chatter():
        while True:
            await scripted_client.emit(*_chatter(1))
            await asyncio.sleep(0.05)

    async def scenario():
        run, pump = await _live("sdk-stop-nowait")
        talking = asyncio.create_task(chatter())
        assert await _settle(lambda: _spans(captured, "assistant_response"))

        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(run.stop(), timeout=10)
        elapsed = asyncio.get_running_loop().time() - started
        talking.cancel()
        return elapsed

    assert _run(scenario()) < 3


class WakeAnswersFirst(ScriptedClient):
    """A child whose finished background task reports in before the prompt it
    was handed has been answered."""

    async def query(self, text):
        self.prompts.append(text)
        await self.emit(
            AssistantMessage(content=[TextBlock("bg task a46 done")]),
            _result(9))
        asyncio.ensure_future(self._answer_after_a_beat())

    async def _answer_after_a_beat(self):
        await asyncio.sleep(0.05)
        await self.emit(
            AssistantMessage(content=[TextBlock("the summary you asked for")]),
            _result(22))


def test_a_one_shot_returns_the_answer_a_wake_result_raced(captured,
                                                           monkeypatch):
    """`run_session` closes a one-shot's queue at launch, so its terminator is
    queued behind the prompt by construction: a wake's result ends the turn and
    the run tears down before its own answer lands. The caller gets nothing,
    under a row that reads `completed`."""
    monkeypatch.setattr(settings.agent_sdk, "teardown_settle_sec", 1)
    fake = _install(monkeypatch, WakeAnswersFirst())

    async def scenario():
        await asyncio.wait_for(
            runner_mod.run_session("sdk-oneshot-wake", "summarise the repo",
                                   one_shot=True), timeout=10)

    _run(scenario())

    assert "the summary you asked for" in [
        s["attributes"]["text"] for s in _spans(captured, "assistant_response")]
    assert _names(captured)[-1] == "session.end"
    assert fake.rows[-1]["status"] == "exited"
    assert fake.rows[-1]["detail"] == "completed"


def test_one_message_cannot_tear_across_two_turns(captured, scripted_client,
                                                  monkeypatch):
    """A span POST is a thread hop, so what is open can change between two
    blocks of the same model message — here the turn is abandoned by a stop
    while its own message is still being written. Both blocks belong to the
    turn the message arrived in; re-reading it per block files one model
    message under two turn identities."""
    import lib.hook_plugin as hook_plugin

    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 0)
    thinking_posted, released = threading.Event(), threading.Event()

    def post_span(**kw):
        captured["spans"].append(kw)
        if kw["name"] == "assistant.thinking":
            thinking_posted.set()
            released.wait(5)
        return True

    monkeypatch.setattr(hook_plugin, "post_span", post_span)

    async def scenario():
        run, pump = await _live("sdk-tear")
        run.enqueue("go")
        assert await _settle(lambda: scripted_client.prompts)
        await scripted_client.emit(AssistantMessage(
            content=[ThinkingBlock(thinking="planning"),
                     TextBlock("and then")]))
        await asyncio.to_thread(thinking_posted.wait, 5)

        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        released.set()
        assert await _settle(lambda: _spans(captured, "assistant_response"))
        await run.stop()

    _run(scenario())

    thinking = _spans(captured, "assistant.thinking")[0]
    said = _spans(captured, "assistant_response")[0]
    assert thinking["attributes"]["turn_uuid"] == "sdk-tear:turn-0"
    assert said["attributes"].get("turn_uuid") == "sdk-tear:turn-0"


# ── the session's own bounds ───────────────────────────────────────────


def test_a_stop_still_ends_a_turn_that_never_answers(captured, scripted_client,
                                                     monkeypatch):
    """A turn ends on its result, and a CLI that sends none leaves one no
    interrupt can be proven to end — so the stop is honoured on a timer."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)

    async def scenario():
        run, pump = await _live("sdk-silent")
        run.enqueue("never answered")
        assert await _settle(lambda: scripted_client.prompts)

        started = asyncio.get_running_loop().time()
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return asyncio.get_running_loop().time() - started

    elapsed = _run(scenario())

    # `stop_grace_sec` is 1 here: the pump is released on the grace period, not
    # on the CLI's cooperation.
    assert elapsed < 4
    assert scripted_client.interrupts == 1
    assert _spans(captured, "session.end")
    assert captured["spans"][-1]["name"] == "session.end"
    assert registry.is_sdk_owned("sdk-silent") is False


def test_background_work_holds_the_idle_timeout_open(captured,
                                                     background_client,
                                                     monkeypatch):
    """`idle_timeout_sec` bounds *silence*, not "nothing queued" — a subagent
    working longer than the timeout would otherwise be reaped mid-tool-call.
    The other half, a genuinely silent session, is
    `test_an_idle_session_releases_itself`.
    """
    monkeypatch.setattr(settings.agent_sdk, "idle_timeout_sec", 1)

    async def chatter():
        for message in _chatter(8):
            await background_client.emit(message)
            await asyncio.sleep(0.25)

    async def scenario():
        run, pump = await _live("sdk-busy")
        talking = asyncio.create_task(chatter())
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(pump, timeout=10)
        elapsed = asyncio.get_running_loop().time() - started
        await talking
        await run.stop()
        return run, elapsed

    run, elapsed = _run(scenario())

    # Every frame reached the trace: the session outlived its own timeout while
    # the agent was still working, and ended only once it fell silent.
    assert len(_spans(captured, "assistant_response")) == 8
    assert elapsed > 2
    assert run._stop_reason == "idle timeout"


# ── a stream that ends ─────────────────────────────────────────────────


def test_a_dead_stream_ends_an_idle_session(captured, scripted_client):
    """`idle_timeout_sec` bounds the wait for an operator, and nothing bounds
    the wait for a result — so a child that exits between turns would hold its
    `max_concurrent_runs` slot behind a `running` row, on a session `/live`
    still offers a composer for."""
    async def scenario():
        run, pump = await _live("sdk-dead")
        await scripted_client.end_stream()

        await asyncio.wait_for(pump, timeout=5)
        refused = registry.submit_prompt("sdk-dead", "anyone there?")
        await run.stop()
        return run, refused

    run, refused = _run(scenario())

    assert run._stop_reason == "agent stream ended"
    assert refused[0] is False
    assert scripted_client.rows[-1]["status"] == "failed"
    assert registry.is_sdk_owned("sdk-dead") is False


def test_a_dead_stream_ends_a_turn_it_can_no_longer_answer(captured,
                                                           scripted_client):
    """The same death with a prompt in flight: the turn fails rather than
    waiting on a result nothing will send."""
    async def scenario():
        task = asyncio.create_task(runner_mod.run_session("sdk-dead2", "go"))
        assert await _settle(lambda: scripted_client.prompts)

        await scripted_client.end_stream()
        with pytest.raises(RuntimeError, match="agent stream ended"):
            await asyncio.wait_for(task, timeout=5)

    _run(scenario())

    assert scripted_client.rows[-1]["status"] == "failed"
    assert scripted_client.rows[-1]["detail"] == "agent stream ended"
    assert registry.is_sdk_owned("sdk-dead2") is False


class DeadOnConnect(ScriptedClient):
    """A child that exits as it starts — a bad cwd, a missing binary, an auth
    failure — with the launch prompt already queued behind it."""

    async def connect(self):
        await super().connect()
        await self.end_stream()


def test_a_launch_whose_child_never_starts_runs_nothing(captured, monkeypatch):
    """The prompt was queued before the death, so releasing the turns in flight
    reaches nothing: without the latch the pump opens a turn for it and waits
    on a result that cannot come, leaving `status='running'` against a live
    pid."""
    fake = _install(monkeypatch, DeadOnConnect())

    async def scenario():
        await asyncio.wait_for(runner_mod.run_session("sdk-stillborn", "go"),
                               timeout=5)

    _run(scenario())

    assert fake.prompts == []
    assert fake.rows[-1]["status"] == "failed"
    assert fake.rows[-1]["detail"] == "agent stream ended"
    assert registry.is_sdk_owned("sdk-stillborn") is False


def test_a_steer_queued_when_the_child_dies_is_never_sent(captured,
                                                          scripted_client,
                                                          monkeypatch):
    """The same latch one turn later: the death lands while the steer is still
    behind the running turn, so the pump reaches it after the release."""
    import lib.hook_plugin as hook_plugin

    billing, released = threading.Event(), threading.Event()

    def post_event(name, payload):
        captured["events"].append((name, payload))
        if name == "turn_usage":
            billing.set()
            released.wait(5)
        return True

    monkeypatch.setattr(hook_plugin, "post_event", post_event)

    async def scenario():
        run, pump = await _live("sdk-dead3")
        run.enqueue("p1")
        assert await _settle(lambda: scripted_client.prompts)
        await scripted_client.emit(_result(2))
        await asyncio.to_thread(billing.wait, 5)

        # Queued behind the turn that is being billed, so the pump only reaches
        # it once the reader has seen the end of the stream.
        run.enqueue("p2")
        await scripted_client.end_stream()
        released.set()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return run

    run = _run(scenario())

    assert scripted_client.prompts == ["p1"]
    assert run._stop_reason == "agent stream ended"
    assert scripted_client.rows[-1]["status"] == "failed"


class ExitsAfterAnswering(BackgroundClient):
    """A child that closes its transport once it has answered, which is what a
    run with nothing more to say gets."""

    async def query(self, text):
        await super().query(text)
        await self.end_stream()


class DiesMidAnswer(ScriptedClient):
    """A child that takes the prompt and dies without answering it."""

    async def query(self, text):
        self.prompts.append(text)
        await self.end_stream()


def test_a_one_shot_that_answered_and_exited_is_not_a_failed_run(captured,
                                                                 monkeypatch):
    """Its queue was closed at launch and its turn is done, so the child
    closing the transport is the ending the run was already headed for."""
    fake = _install(monkeypatch, ExitsAfterAnswering())

    async def scenario():
        await asyncio.wait_for(
            runner_mod.run_session("sdk-oneshot-clean", "go", one_shot=True),
            timeout=5)

    _run(scenario())

    assert fake.rows[-1]["status"] == "exited"
    assert fake.rows[-1]["detail"] == "completed"


def test_a_one_shot_that_died_mid_answer_is_still_a_failed_run(captured,
                                                               monkeypatch):
    """The closed queue alone must not read as a clean ending: this run's turn
    was still owed a result."""
    fake = _install(monkeypatch, DiesMidAnswer())

    async def scenario():
        with pytest.raises(RuntimeError, match="agent stream ended"):
            await asyncio.wait_for(
                runner_mod.run_session("sdk-oneshot-dead", "go",
                                       one_shot=True), timeout=5)

    _run(scenario())

    assert fake.rows[-1]["status"] == "failed"


class StopClosesStream(ScriptedClient):
    """A CLI whose transport goes with the interrupt — the SDK sends its end
    sentinel from the `finally` of every transport exit."""

    async def interrupt(self):
        self.interrupts += 1
        await self.end_stream()


def test_a_stop_that_closes_the_stream_is_not_a_failed_run(captured,
                                                           monkeypatch):
    """The operator asked for this ending, so the run row must read it as one:
    a Stop filed as a crash misreports the run and pushes the exception into
    the supervisor's completion callback."""
    fake = _install(monkeypatch, StopClosesStream())

    async def scenario():
        task = asyncio.create_task(runner_mod.run_session("sdk-stopclose",
                                                          "go"))
        assert await _settle(lambda: fake.prompts)

        assert registry.stop_run("sdk-stopclose")[0] is True
        await asyncio.wait_for(task, timeout=5)

    _run(scenario())

    assert fake.rows[-1]["status"] == "exited"
    assert fake.rows[-1]["detail"] == "stopped"
    assert _names(captured)[-1] == "session.end"
