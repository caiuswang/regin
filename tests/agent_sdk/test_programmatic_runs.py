"""Runs regin launches for itself (`lib/agent_sdk/supervisor`, `client`).

The `/live` launcher hands an operator's prompt to a session it then watches.
A *programmatic* caller needs two things that path never did: an environment on
the launched agent (regin's own spawns talk to their agent through env vars),
and a way to learn the run ended without holding a request thread against it.
These tests drive a fake SDK client through the shared loop, so nothing here
spawns a real agent.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from dataclasses import dataclass, field

import pytest

from lib.agent_sdk import client, registry, runner as runner_mod, supervisor
from lib.settings import settings

# An id a test launches under twice, standing in for a resumed run's own.
_HELD = "sdk-held00001"


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
    def __init__(self):
        self.prompts: list[str] = []
        self.disconnects = 0

    async def connect(self):
        pass

    async def query(self, text):
        self.prompts.append(text)

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock("drafted")])
        yield ResultMessage()

    async def interrupt(self):
        pass

    async def disconnect(self):
        self.disconnects += 1


@pytest.fixture(autouse=True)
def quiet_ingest(monkeypatch):
    import lib.hook_plugin as hook_plugin

    monkeypatch.setattr(hook_plugin, "post_span", lambda **kw: True)
    monkeypatch.setattr(hook_plugin, "post_event", lambda name, payload: True)


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    cli = tmp_path / "claude"
    cli.write_text("")
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "cli_path", str(cli))
    monkeypatch.setattr(settings.agent_sdk, "model", "")
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "default")


@pytest.fixture(autouse=True)
def sdk_store(monkeypatch):
    """Back `agent_runs` with a dict for this module.

    These tests drive the REAL supervisor, whose runs execute on a shared
    daemon loop. That loop's DB writes do not go through `tmp_db` — they land
    in the developer's own `db/regin.db`, and a write that arrives after the
    test returns lands there whatever the test did. Keeping the store in
    memory removes the write entirely rather than racing it.
    """
    rows: dict[str, dict] = {}

    def _upsert(trace_id, *, status, **fields):
        row = rows.setdefault(trace_id, {"trace_id": trace_id})
        row["status"] = status
        row.update({k: v for k, v in fields.items() if v is not None})

    monkeypatch.setattr(runner_mod.store, "upsert_run", _upsert)
    monkeypatch.setattr(runner_mod.store, "get_run", lambda tid: rows.get(tid))
    return rows


@pytest.fixture(autouse=True)
def _free_the_held_id():
    yield
    registry.unregister_run(_HELD)


@pytest.fixture
def fake_client(monkeypatch, enabled):
    fake = FakeClient()
    monkeypatch.setattr(runner_mod.client, "new_client", lambda **kw: fake)
    return fake


# ── option passthrough ────────────────────────────────────────────────


def test_a_run_carries_its_own_env_permission_mode_and_model(enabled):
    options = client.RunOptions(env={"REGIN_LLM_SURFACE": "drafting"},
                                permission_mode="bypassPermissions",
                                model="claude-haiku-4-5")

    built = client.build_options(cwd="/repo", options=options)

    assert built.env["REGIN_LLM_SURFACE"] == "drafting"
    assert built.permission_mode == "bypassPermissions"
    assert built.model == "claude-haiku-4-5"
    assert built.cwd == "/repo"


def test_without_overrides_the_global_settings_still_decide(enabled,
                                                            monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "model", "claude-opus-5")
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "acceptEdits")

    built = client.build_options()

    assert built.env == {"REGIN_BRIDGE": "0"}
    assert built.model == "claude-opus-5"
    assert built.permission_mode == "acceptEdits"


def test_two_concurrent_runs_do_not_read_each_others_env(enabled):
    """The shared loop runs every session in one thread, so a run's options
    must travel with the run — anything ambient would leak one run's
    environment into another's agent."""
    async def bind(surface, seen):
        options = client.RunOptions(env={"S": surface})
        await asyncio.sleep(0)
        seen.append(client.build_options(options=options).env["S"])

    async def scenario():
        seen: list[str] = []
        await asyncio.gather(bind("first", seen), bind("second", seen))
        return seen

    assert sorted(asyncio.run(scenario())) == ["first", "second"]


def test_a_launched_runs_env_reaches_the_agent_it_starts(enabled, monkeypatch):
    """End to end over the shared loop: the overrides are bound on the caller's
    thread but have to be readable where the run builds its client."""
    built = {}

    def _new_client(**kwargs):
        built["options"] = client.build_options(**kwargs)
        return FakeClient()

    monkeypatch.setattr(runner_mod.client, "new_client", _new_client)

    supervisor.launch_run(
        "draft it", cwd="/repo", one_shot=True,
        env={"REGIN_LLM_SURFACE": "topic-proposal-drafting"},
        permission_mode="bypassPermissions").wait(timeout=10)

    options = built["options"]
    assert options.env["REGIN_LLM_SURFACE"] == "topic-proposal-drafting"
    assert options.permission_mode == "bypassPermissions"
    assert options.cwd == "/repo"


# ── completion signal ─────────────────────────────────────────────────


def test_a_one_shot_run_ends_with_its_turn(fake_client):
    handle = supervisor.launch_run("draft it", one_shot=True)

    outcome = handle.wait(timeout=10)

    assert fake_client.prompts == ["draft it"]
    assert fake_client.disconnects == 1
    assert outcome.trace_id == handle.trace_id
    assert outcome.status == "exited"
    assert outcome.detail == "completed"


def test_the_run_row_records_the_same_outcome(fake_client):
    from lib.agent_sdk import store

    handle = supervisor.launch_run("draft it", one_shot=True)
    handle.wait(timeout=10)

    assert store.get_run(handle.trace_id)["status"] == "exited"


def test_launch_returns_before_the_run_finishes(fake_client):
    handle = supervisor.launch_run("draft it", one_shot=True)

    assert handle.trace_id.startswith("sdk-")
    handle.wait(timeout=10)


def test_a_callback_reports_the_outcome_without_waiting(fake_client):
    import threading

    seen = []
    done = threading.Event()
    handle = supervisor.launch_run("draft it", one_shot=True)
    handle.add_done_callback(lambda outcome: (seen.append(outcome),
                                              done.set()))

    assert done.wait(10) is True
    assert seen[0].status == "exited"


def test_a_crashed_run_is_reported_as_failed_not_raised(fake_client,
                                                        monkeypatch):
    async def boom(text):
        raise RuntimeError("client died")

    monkeypatch.setattr(fake_client, "query", boom)

    outcome = supervisor.launch_run("draft it", one_shot=True).wait(timeout=10)

    assert outcome.status == "failed"
    assert "client died" in outcome.detail


def test_waiting_past_the_ceiling_raises_rather_than_reporting_an_end(
        fake_client, monkeypatch):
    """A timeout is a run still going, not a run that failed — the caller
    decides whether to stop it."""
    released = asyncio.Event()

    async def slow(text):
        await released.wait()

    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 0)
    monkeypatch.setattr(fake_client, "query", slow)
    handle = supervisor.launch_run("draft it", one_shot=True)

    with pytest.raises(TimeoutError):
        handle.wait(timeout=0.2)

    handle.stop()
    assert handle.wait(timeout=10).detail == "stopped"


def test_an_interactive_launch_still_returns_a_trace_id(monkeypatch):
    """`launch` is what the `/live` route calls; adding the programmatic entry
    point must not have changed its shape. Asserted against a stubbed
    `launch_run` rather than a real run: `launch` is fire-and-forget, so a live
    one would still be tearing down on the shared loop after the test returned.
    """
    class _Handle:
        trace_id = "sdk-deadbeef"

    seen = {}

    def _launch_run(prompt, **kwargs):
        seen.update({"prompt": prompt, **kwargs})
        return _Handle()

    monkeypatch.setattr(supervisor, "launch_run", _launch_run)

    trace_id = supervisor.launch("hello", cwd="/repo")

    assert trace_id == "sdk-deadbeef"
    assert seen == {"prompt": "hello", "cwd": "/repo", "model": "",
                    "permission_mode": "", "one_shot": False, "resume": None,
                    "trace_id": None}

def test_a_disabled_tier_refuses_a_programmatic_launch(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    with pytest.raises(supervisor.LaunchRefused):
        supervisor.launch_run("draft it", one_shot=True)


# ── the id a launch holds ─────────────────────────────────────────────
#
# A runner registers only after `connect()` has spawned its child. A launch
# therefore claims its trace id up front, or a second launch reusing that id —
# which is what a resume is — would be admitted into the gap.


class StallingClient(FakeClient):
    """Parks inside `connect()`: the run is scheduled and running, but its
    runner has not registered."""

    def __init__(self, connecting, release):
        super().__init__()
        self._connecting = connecting
        self._release = release

    async def connect(self):
        self._connecting.set()
        while not self._release.is_set():
            await asyncio.sleep(0.01)


def test_a_run_still_starting_already_owns_its_trace_id(enabled, monkeypatch):
    connecting, release = threading.Event(), threading.Event()
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: StallingClient(connecting, release))
    handle = supervisor.launch_run("draft it", trace_id=_HELD, one_shot=True)
    try:
        assert connecting.wait(5) is True

        assert registry.is_sdk_owned(_HELD) is True
    finally:
        release.set()
    handle.wait(timeout=10)


def test_a_second_launch_on_a_starting_runs_id_is_refused(enabled, monkeypatch):
    """Two resumes of one stopped run, arriving together: admitting the second
    leaves two runners on one id, and the first to tear down evicts the other's
    entry — a live child `/live` can no longer stop or steer."""
    connecting, release = threading.Event(), threading.Event()
    made = []

    def _new_client(**kwargs):
        made.append(StallingClient(connecting, release))
        return made[-1]

    monkeypatch.setattr(runner_mod.client, "new_client", _new_client)
    handle = supervisor.launch_run("draft it", trace_id=_HELD, one_shot=True)
    try:
        assert connecting.wait(5) is True

        with pytest.raises(supervisor.LaunchRefused, match="already starting"):
            supervisor.launch_run("draft it too", trace_id=_HELD,
                                  one_shot=True)
    finally:
        release.set()
    handle.wait(timeout=10)
    assert len(made) == 1


def test_a_starting_run_counts_against_capacity(enabled, monkeypatch):
    connecting, release = threading.Event(), threading.Event()
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: StallingClient(connecting, release))
    monkeypatch.setattr(settings.agent_sdk, "max_concurrent_runs", 1)
    handle = supervisor.launch_run("draft it", trace_id=_HELD, one_shot=True)
    try:
        assert connecting.wait(5) is True

        with pytest.raises(supervisor.LaunchRefused,
                           match="max_concurrent_runs"):
            supervisor.launch_run("draft it too", one_shot=True)
    finally:
        release.set()
    # The run's own reservation is not one of the slots it is competing for.
    assert handle.wait(timeout=10).status == "exited"


def test_a_finished_run_leaves_its_id_free_to_launch_again(fake_client):
    supervisor.launch_run("draft it", trace_id=_HELD,
                          one_shot=True).wait(timeout=10)

    again = supervisor.launch_run("carry on", trace_id=_HELD, one_shot=True)

    assert again.wait(timeout=10).status == "exited"


def test_a_crashed_run_leaves_its_id_free_to_launch_again(fake_client,
                                                          monkeypatch):
    turns = []

    async def dies_once(text):
        turns.append(text)
        if len(turns) == 1:
            raise RuntimeError("client died")

    monkeypatch.setattr(fake_client, "query", dies_once)

    crashed = supervisor.launch_run("draft it", trace_id=_HELD,
                                    one_shot=True).wait(timeout=10)
    resumed = supervisor.launch_run("carry on", trace_id=_HELD,
                                    one_shot=True).wait(timeout=10)

    assert crashed.status == "failed"
    assert resumed.status == "exited"


def test_a_run_that_never_reached_its_runner_still_gives_the_id_back(
        enabled, monkeypatch):
    """No runner means no `unregister_run`, so the launch's own completion
    callback is the only thing that can hand the id back."""
    async def _explode(trace_id, prompt, **kwargs):
        raise RuntimeError("never started")

    monkeypatch.setattr(supervisor, "run_session", _explode)

    outcome = supervisor.launch_run("draft it", trace_id=_HELD,
                                    one_shot=True).wait(timeout=10)

    assert outcome.status == "failed"
    assert registry.is_sdk_owned(_HELD) is False


def test_a_launch_that_cannot_be_scheduled_gives_the_id_back(enabled,
                                                             monkeypatch):
    def _no_loop():
        raise RuntimeError("loop is gone")

    monkeypatch.setattr(supervisor, "_ensure_loop", _no_loop)

    with pytest.raises(RuntimeError, match="loop is gone"):
        supervisor.launch_run("draft it", trace_id=_HELD, one_shot=True)

    assert registry.is_sdk_owned(_HELD) is False


def test_a_cancelled_run_gives_the_id_back():
    """Cancellation is a terminal path like any other, and the one that skips
    the coroutine entirely — an id held through it is held forever."""
    token = object()
    registry.reserve_run(_HELD, token)
    future = concurrent.futures.Future()
    future.add_done_callback(lambda f: supervisor._on_done(_HELD, token, f))

    assert future.cancel() is True

    assert registry.is_sdk_owned(_HELD) is False
