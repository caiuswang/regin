"""Steering a regin-launched agent from the id its own hooks report.

A launched run is traced twice: as the run (`sdk-…`), and as the `claude`
session the child announces through the user's hooks. An operator on `/live`
lands on either, so both have to reach the one live channel — otherwise the
child id falls through to the tmux bridge, which resolves to the pane regin's
*server* runs in and refuses with "pane runs 'Python', not claude" (or, worse,
types into the operator's own terminal).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import pytest

from lib.agent_sdk import client, registry, runner as runner_mod
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
    session_id: str | None = None


@dataclass
class SystemMessage:
    subtype: str = "init"
    data: dict = field(default_factory=dict)


@dataclass
class ResultMessage:
    is_error: bool = False
    result: str | None = None
    duration_ms: int = 0
    usage: dict | None = None
    session_id: str | None = None


_END = object()


class FakeClient:
    """Replays `messages` for every prompt, calling `probe` after each one has
    been handled — the only window in which a run is still registered, since
    the alias is dropped with the session it names."""

    def __init__(self, messages, probe=None):
        self.messages = messages
        self.probe = probe
        self.prompts: list[str] = []
        self.turns: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        return None

    async def query(self, text):
        self.prompts.append(text)
        self.turns.put_nowait(self.messages)

    async def receive_messages(self):
        while True:
            turn = await self.turns.get()
            if turn is _END:
                return
            for message in turn:
                yield message
                if self.probe:
                    self.probe()

    async def interrupt(self):
        return None

    async def disconnect(self):
        self.turns.put_nowait(_END)


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run("sdk-run")


@pytest.fixture
def quiet(monkeypatch):
    """Silence the span/usage sinks and the run row."""
    import lib.hook_plugin as hook_plugin

    monkeypatch.setattr(hook_plugin, "post_span", lambda **kw: True)
    monkeypatch.setattr(hook_plugin, "post_event", lambda name, payload: True)
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


class _LiveRun:
    """A registered runner backed by a real loop on its own thread — the cross-
    thread hop `submit_prompt` makes is the point, and a fake loop would pass
    against a bug that never delivers."""

    def __init__(self):
        self.queued: list[str] = []
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def enqueue(self, text: str) -> None:
        self.queued.append(text)

    def wait_for_queued(self, timeout: float = 5.0) -> list[str]:
        """Let the loop run the scheduled callback before reading the queue."""
        asyncio.run_coroutine_threadsafe(
            asyncio.sleep(0), self.loop).result(timeout=timeout)
        return self.queued

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


@pytest.fixture
def live_run():
    run = _LiveRun()
    registry.register_run("sdk-run", run)
    yield run
    run.close()


def _run_with(monkeypatch, messages, watch=""):
    """Run one turn over `messages`, returning what `watch` resolved to while
    the run was still live."""
    seen: list[str] = []
    probe = (lambda: seen.append(registry.owning_run(watch))) if watch else None
    fake = FakeClient(messages, probe=probe)
    monkeypatch.setattr(runner_mod.client, "new_client", lambda **kw: fake)
    asyncio.run(runner_mod.run_session("sdk-run", "hello", one_shot=True))
    return seen


# ── the alias ─────────────────────────────────────────────────────────


def test_the_childs_own_session_id_reaches_the_run(quiet, monkeypatch):
    resolved = _run_with(monkeypatch, [
        AssistantMessage(content=[TextBlock("hi")], session_id="cli-7794"),
        ResultMessage(),
    ], watch="cli-7794")

    assert resolved and resolved[0] == "sdk-run"


def test_an_init_message_is_enough_to_learn_it(quiet, monkeypatch):
    """The id arrives on the SDK's init frame before any assistant text, so a
    run that parks on its first tool call is steerable too."""
    resolved = _run_with(monkeypatch, [
        SystemMessage(data={"session_id": "cli-init"}),
        ResultMessage(),
    ], watch="cli-init")

    assert resolved and resolved[0] == "sdk-run"


def test_a_prompt_sent_to_the_child_id_lands_on_the_runs_queue(live_run):
    """What the `/live` composer does: the id in the URL is the child's, and
    the prompt has to end up on the queue of the run holding the channel."""
    registry.register_alias("cli-live", "sdk-run")

    assert registry.is_sdk_owned("cli-live") is True
    assert registry.submit_prompt("cli-live", "keep going") == (True,
                                                                "prompt queued")
    assert live_run.wait_for_queued() == ["keep going"]


def test_the_alias_dies_with_the_run(quiet, monkeypatch):
    """A stale alias would keep claiming a channel nobody holds — and the id it
    names is a real session someone may later steer over the bridge."""
    resolved = _run_with(monkeypatch, [
        AssistantMessage(content=[TextBlock("hi")], session_id="cli-gone"),
        ResultMessage(),
    ], watch="cli-gone")

    assert resolved and resolved[0] == "sdk-run"
    assert registry.is_sdk_owned("cli-gone") is False
    assert registry.owning_run("cli-gone") == "cli-gone"


def test_an_unknown_id_is_left_alone(quiet):
    assert registry.owning_run("some-other-session") == "some-other-session"
    assert registry.is_sdk_owned("some-other-session") is False


# ── the child's pane ──────────────────────────────────────────────────


def test_the_child_never_registers_the_launchers_tmux_pane():
    """`TMUX_PANE` is inherited, so without this the child's SessionStart hook
    registers the pane regin's server runs in as the session's bridge pane."""
    pytest.importorskip("claude_agent_sdk")

    assert client.build_options().env["REGIN_BRIDGE"] == "0"


def test_a_runs_own_env_still_travels_with_it():
    pytest.importorskip("claude_agent_sdk")

    options = client.RunOptions(env={"REGIN_LLM_SURFACE": "drafting"})

    built = client.build_options(options=options)

    assert built.env == {"REGIN_BRIDGE": "0", "REGIN_LLM_SURFACE": "drafting"}
