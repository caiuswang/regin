"""The runner as a *session* rather than a one-shot (`lib/agent_sdk/runner`).

`run_once` connected, sent one prompt, and tore down — there was no way to say
anything else to the agent. These tests drive a fake SDK client through the
queue-and-pump path: a second prompt must run on the same connection, the
session must stay owned between turns, and every turn's spend must reach
`turn_usage` with its own row identity.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from lib.agent_sdk import registry, runner as runner_mod
from lib.settings import settings


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list
    model: str | None = "claude-opus-5"
    parent_tool_use_id: str | None = None
    usage: dict | None = None


@dataclass
class ResultMessage:
    is_error: bool = False
    result: str | None = None
    duration_ms: int = 0
    usage: dict | None = field(default_factory=lambda: {
        "input_tokens": 7, "output_tokens": 2,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
    })


class FakeClient:
    """Answers every prompt with one sentence and a result."""

    def __init__(self):
        self.connects = 0
        self.disconnects = 0
        self.interrupts = 0
        self.prompts: list[str] = []

    async def connect(self):
        self.connects += 1

    async def query(self, text):
        self.prompts.append(text)

    async def receive_response(self):
        for message in (
            AssistantMessage(content=[TextBlock(f"answer {len(self.prompts)}")]),
            ResultMessage(),
        ):
            yield message

    async def interrupt(self):
        self.interrupts += 1

    async def disconnect(self):
        self.disconnects += 1


@pytest.fixture
def captured(monkeypatch):
    """Intercept both sinks — spans and turn_usage events."""
    import lib.hook_plugin as hook_plugin

    spans: list[dict] = []
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(hook_plugin, "post_span",
                        lambda **kw: spans.append(kw) or True)
    monkeypatch.setattr(hook_plugin, "post_event",
                        lambda name, payload: events.append((name, payload)) or True)
    return {"spans": spans, "events": events}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: client)
    monkeypatch.setattr(runner_mod.store, "upsert_run",
                        lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "model", "claude-opus-5")
    return client


def _names(spans):
    return [s["name"] for s in spans]


def _run(coro):
    return asyncio.run(coro)


async def _session(trace_id, prompts, *, stop=True):
    """Start a runner, feed it `prompts`, and let the pump drain them."""
    run = runner_mod.AgentRunner(trace_id)
    await run.start()
    for text in prompts:
        run.enqueue(text)
    if stop:
        run.close()
    await run.pump()
    await run.stop()
    return run


def test_a_turn_records_what_the_agent_said(captured, fake_client):
    _run(_session("sdk-t1", ["hello"]))

    names = _names(captured["spans"])
    assert "assistant_response" in names
    said = next(s for s in captured["spans"] if s["name"] == "assistant_response")
    assert said["attributes"]["text"] == "answer 1"


def test_session_boundaries_bracket_the_run(captured, fake_client):
    _run(_session("sdk-t2", ["hello"]))

    names = _names(captured["spans"])
    assert names[0] == "session.start"
    assert names[-1] == "session.end"


def test_a_run_with_no_prompt_still_has_both_boundaries(captured, fake_client):
    _run(_session("sdk-t3", []))

    assert _names(captured["spans"]) == ["session.start", "session.end"]


def test_a_second_prompt_runs_on_the_same_connection(captured, fake_client):
    _run(_session("sdk-t4", ["first", "second"]))

    assert fake_client.prompts == ["first", "second"]
    # One connect for two turns — the session was not torn down and relaunched.
    assert fake_client.connects == 1
    assert _names(captured["spans"]).count("prompt") == 2


def test_each_turn_posts_its_own_usage_row(captured, fake_client):
    _run(_session("sdk-t5", ["first", "second"]))

    rows = [r for name, payload in captured["events"] if name == "turn_usage"
            for r in payload]
    assert len(rows) == 2
    assert rows[0]["turn_uuid"] != rows[1]["turn_uuid"]
    assert rows[0]["context_used_tokens"] == 107
    assert rows[0]["model"] == "claude-opus-5"


def test_the_session_stays_owned_between_turns(captured, fake_client):
    seen = []

    async def scenario():
        run = runner_mod.AgentRunner("sdk-t6")
        await run.start()
        run.enqueue("first")
        pump = asyncio.create_task(run.pump())
        await asyncio.sleep(0)
        seen.append(registry.is_sdk_owned("sdk-t6"))
        run.close()
        await pump
        await run.stop()
        seen.append(registry.is_sdk_owned("sdk-t6"))

    _run(scenario())

    assert seen == [True, False]


def test_an_idle_session_releases_itself(captured, fake_client, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "idle_timeout_sec", 1)

    async def scenario():
        run = runner_mod.AgentRunner("sdk-t7")
        await run.start()
        # Never enqueue and never close: only the idle timeout can end this.
        await asyncio.wait_for(run.pump(), timeout=5)
        await run.stop()
        return run

    run = _run(scenario())

    assert run._stop_reason == "idle timeout"
    assert fake_client.disconnects == 1


def test_run_session_keeps_the_session_open_after_its_first_prompt(
        captured, fake_client):
    """The regression the tier shipped with: `run_once` tore down here."""
    async def scenario():
        task = asyncio.create_task(
            runner_mod.run_session("sdk-t8", "first"))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if fake_client.prompts:
                break
        owned_mid = registry.is_sdk_owned("sdk-t8")
        assert registry.submit_prompt("sdk-t8", "second")[0] is True
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(fake_client.prompts) == 2:
                break
        registry.stop_run("sdk-t8")
        await asyncio.wait_for(task, timeout=5)
        return owned_mid

    owned_mid = _run(scenario())

    assert owned_mid is True
    assert fake_client.prompts == ["first", "second"]
    assert registry.is_sdk_owned("sdk-t8") is False


@dataclass
class ThinkingBlock:
    thinking: str = ''
    signature: str = ''


class SubagentClient(FakeClient):
    """A turn whose Task subagent answers on a cheaper model, and whose two
    API calls report different prompt sizes."""

    async def receive_response(self):
        for message in (
            AssistantMessage(
                content=[ThinkingBlock(thinking="planning")],
                usage={"input_tokens": 2, "cache_read_input_tokens": 0,
                       "cache_creation_input_tokens": 30663},
            ),
            AssistantMessage(
                content=[TextBlock("sub says hi")], model="claude-haiku-4-5",
                parent_tool_use_id="toolu_task",
                usage={"input_tokens": 99, "cache_read_input_tokens": 999999},
            ),
            AssistantMessage(
                content=[TextBlock("done")],
                usage={"input_tokens": 2, "cache_read_input_tokens": 30663,
                       "cache_creation_input_tokens": 367},
            ),
            ResultMessage(usage={
                "input_tokens": 4, "output_tokens": 288,
                "cache_read_input_tokens": 30663,
                "cache_creation_input_tokens": 31030,
            }),
        ):
            yield message


@pytest.fixture
def subagent_client(monkeypatch):
    client = SubagentClient()
    monkeypatch.setattr(runner_mod.client, "new_client", lambda **kw: client)
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "model", "claude-opus-5")
    return client


def _usage_rows(captured):
    return [r for name, payload in captured["events"] if name == "turn_usage"
            for r in payload]


@pytest.fixture
def subagent_run(captured, subagent_client):
    _run(_session("sdk-sub", ["go"]))
    return captured


def test_a_subagents_model_does_not_reprice_the_parents_turn(subagent_run):
    assert _usage_rows(subagent_run)[0]["model"] == "claude-opus-5"


def test_context_is_the_last_calls_prompt_not_the_turns_traffic(subagent_run):
    row = _usage_rows(subagent_run)[0]

    # Naive sum of the turn totals would be 4 + 30663 + 31030 = 61697.
    assert row["context_used_tokens"] == 2 + 30663 + 367
    assert row["output_tokens"] == 288


def test_a_subagents_call_usage_never_moves_the_parents_context(subagent_run):
    """The subagent call reports a ~1M prompt; it must not land as the
    parent session's context high-water mark."""
    assert _usage_rows(subagent_run)[0]["context_used_tokens"] < 100_000


def test_assistant_spans_carry_the_turn_identity(subagent_run):
    spans = [s for s in subagent_run["spans"]
             if s["name"] in ("assistant_response", "assistant.thinking")]

    assert spans
    for span in spans:
        assert span["attributes"]["turn_uuid"] == "sdk-sub:turn-0"
        assert span["attributes"]["turn_index"] == 0


class InterruptedClient(FakeClient):
    """A turn the operator cut short — real tokens spent, no answer."""

    async def receive_response(self):
        yield AssistantMessage(
            content=[TextBlock("half an ans")],
            usage={"input_tokens": 2, "cache_read_input_tokens": 5000},
        )
        yield ResultMessage(is_error=True, result="Interrupted by user",
                            usage={"input_tokens": 2, "output_tokens": 512,
                                   "cache_read_input_tokens": 5000,
                                   "cache_creation_input_tokens": 0})


def test_an_interrupted_turn_still_records_its_spend(captured, fake_client,
                                                     monkeypatch):
    """The control-plane action this tier adds must not lose the turn's cost."""
    client = InterruptedClient()
    monkeypatch.setattr(runner_mod.client, "new_client", lambda **kw: client)

    _run(_session("sdk-interrupted", ["go"]))

    rows = _usage_rows(captured)
    assert len(rows) == 1
    assert rows[0]["output_tokens"] == 512
    assert rows[0]["context_used_tokens"] == 5002
    # The failure is visible as a span as well as a cost.
    assert "turn.cancel" in _names(captured["spans"])


def test_a_stopping_session_refuses_further_prompts(captured, fake_client):
    """`is_sdk_owned` stays true through teardown, so reachability alone would
    accept a prompt this session will never run."""
    async def scenario():
        run = runner_mod.AgentRunner("sdk-stopping")
        await run.start()
        run.enqueue("first")
        pump = asyncio.create_task(run.pump())
        await run.request_stop()
        result = registry.submit_prompt("sdk-stopping", "too late")
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return result

    delivered, detail = _run(scenario())

    assert delivered is False
    assert detail == "agent session is stopping"
    assert "too late" not in fake_client.prompts


def test_the_run_row_learns_the_model_the_agent_answered_on(captured,
                                                            fake_client,
                                                            monkeypatch):
    """`agent_sdk.model` is empty by default, so the row is created with no
    model and would keep claiming none while its usage rows name one."""
    monkeypatch.setattr(settings.agent_sdk, "model", "")
    models = []
    monkeypatch.setattr(runner_mod.store, "upsert_run",
                        lambda tid, **kw: models.append(kw.get("model")))

    _run(_session("sdk-model", ["go"]))

    assert "claude-opus-5" in models


class WedgedClient(FakeClient):
    """A turn that never yields a result — the shape the SDK documents as
    iterating indefinitely, and the one a stop has to survive."""

    def __init__(self):
        super().__init__()
        self.released = asyncio.Event()

    async def receive_response(self):
        await self.released.wait()
        yield ResultMessage()

    async def interrupt(self):
        self.interrupts += 1
        # A CLI that ignores the interrupt: the stream stays open.


@pytest.fixture
def wedged_client(monkeypatch):
    client = WedgedClient()
    monkeypatch.setattr(runner_mod.client, "new_client", lambda **kw: client)
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    return client


def test_stop_ends_a_session_wedged_mid_turn(captured, wedged_client):
    """`stop` must not report success for a session it cannot actually end."""
    async def scenario():
        run = runner_mod.AgentRunner("sdk-wedged")
        await run.start()
        run.enqueue("hangs forever")
        pump = asyncio.create_task(run.pump())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if wedged_client.prompts:
                break
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=6)
        await run.stop()

    _run(scenario())

    assert wedged_client.interrupts == 1
    assert wedged_client.disconnects == 1
    assert registry.is_sdk_owned("sdk-wedged") is False
    assert _names(captured["spans"])[-1] == "session.end"


def test_an_interrupt_that_works_ends_the_turn_without_the_grace_wait(
        captured, wedged_client):
    async def scenario():
        run = runner_mod.AgentRunner("sdk-wedged2")
        await run.start()
        run.enqueue("hangs until interrupted")
        pump = asyncio.create_task(run.pump())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if wedged_client.prompts:
                break
        wedged_client.released.set()
        await run.request_stop()
        await asyncio.wait_for(pump, timeout=3)
        await run.stop()

    _run(scenario())

    # The turn completed, so its usage still landed — a stop is not a discard.
    rows = [r for name, payload in captured["events"] if name == "turn_usage"
            for r in payload]
    assert len(rows) == 1


def test_the_launch_prompt_cannot_be_overtaken(captured, fake_client):
    """A follow-up arriving while `start()` is still connecting must queue
    behind the prompt the run was launched for."""
    async def scenario():
        task = asyncio.create_task(runner_mod.run_session("sdk-order", "FIRST"))
        for _ in range(100):
            await asyncio.sleep(0.005)
            if registry.is_sdk_owned("sdk-order"):
                break
        registry.submit_prompt("sdk-order", "SECOND")
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(fake_client.prompts) == 2:
                break
        registry.stop_run("sdk-order")
        await asyncio.wait_for(task, timeout=6)

    _run(scenario())

    assert fake_client.prompts == ["FIRST", "SECOND"]


def test_stop_is_idempotent(captured, fake_client):
    async def scenario():
        run = runner_mod.AgentRunner("sdk-twice")
        await run.start()
        await run.stop()
        await run.stop()

    _run(scenario())

    assert _names(captured["spans"]).count("session.end") == 1
    assert fake_client.disconnects == 1


def test_the_row_is_written_even_when_teardown_fails(captured, fake_client,
                                                     monkeypatch):
    """A `running` row nothing backs outlives the process and misleads every
    later reader, so it must be closed even on a broken disconnect."""
    async def boom():
        raise RuntimeError("socket already gone")

    monkeypatch.setattr(fake_client, "disconnect", boom)
    statuses = []
    monkeypatch.setattr(runner_mod.store, "upsert_run",
                        lambda tid, **kw: statuses.append(kw.get("status")))

    async def scenario():
        run = runner_mod.AgentRunner("sdk-brokenstop")
        await run.start()
        with pytest.raises(RuntimeError):
            await run.stop()

    _run(scenario())

    assert statuses[-1] == "exited"


def test_a_failed_turn_ends_the_session_as_failed(captured, fake_client,
                                                  monkeypatch):
    async def boom(text):
        raise RuntimeError("client died")

    monkeypatch.setattr(fake_client, "query", boom)
    statuses = []
    monkeypatch.setattr(runner_mod.store, "upsert_run",
                        lambda tid, **kw: statuses.append(kw.get("status")))

    with pytest.raises(RuntimeError):
        _run(runner_mod.run_session("sdk-t9", "hello"))

    assert statuses[-1] == "failed"
    assert registry.is_sdk_owned("sdk-t9") is False


def test_an_unattended_park_is_declined_rather_than_held_forever(
        captured, fake_client, monkeypatch):
    """`idle_timeout_sec` bounds the wait BETWEEN turns; a park lives inside
    one, so an unattended run would hold its slot until the server restarted."""
    monkeypatch.setattr(settings.agent_sdk, "park_timeout_sec", 1)
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", ["Bash"])

    class Ctx:
        tool_use_id = "tu-unattended"

    async def scenario():
        run = runner_mod.AgentRunner("sdk-unattended")
        run.loop = asyncio.get_running_loop()
        run._post = _noop_post
        return await asyncio.wait_for(
            run._can_use_tool("Bash", {"command": "ls"}, Ctx()), timeout=6)

    result = _run(scenario())

    # Nobody said yes, so the call is declined — not approved by default.
    assert type(result).__name__ == "PermissionResultDeny"
    assert registry.pending_asks("sdk-unattended") == []


def test_a_one_shot_run_refuses_a_follow_up_it_could_never_run(
        captured, fake_client):
    """Its queue is closed at launch, so a queued prompt would sit behind the
    terminator forever — reporting it as queued is a lie."""
    async def scenario():
        task = asyncio.create_task(
            runner_mod.run_session("sdk-oneshot", "the job", one_shot=True))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if registry.is_sdk_owned("sdk-oneshot"):
                break
        result = registry.submit_prompt("sdk-oneshot", "and another")
        await asyncio.wait_for(task, timeout=6)
        return result

    delivered, detail = _run(scenario())

    assert delivered is False
    assert fake_client.prompts == ["the job"]


def test_the_runner_will_not_start_past_max_concurrent_runs(
        captured, fake_client, monkeypatch):
    """The supervisor refuses a launch before scheduling it; this gate catches
    a runner constructed directly, which the programmatic API allows."""
    monkeypatch.setattr(settings.agent_sdk, "max_concurrent_runs", 1)
    registry.register_run("sdk-occupant", object())
    try:
        with pytest.raises(runner_mod.RunnerBusy):
            _run(runner_mod.AgentRunner("sdk-capped").start())
    finally:
        registry.unregister_run("sdk-occupant")

    assert fake_client.connects == 0


def test_a_runs_own_claim_does_not_deny_it_the_last_slot(
        captured, fake_client, monkeypatch):
    """A launch reserves its id before the runner exists, so counting that
    reservation would make the run compete with itself for the slot it holds."""
    monkeypatch.setattr(settings.agent_sdk, "max_concurrent_runs", 1)
    token = object()
    assert registry.reserve_run("sdk-claimed", token) is True
    try:
        _run(runner_mod.AgentRunner("sdk-claimed").start())
        assert fake_client.connects == 1
    finally:
        registry.unregister_run("sdk-claimed")


async def _noop_post(_span):
    return None
