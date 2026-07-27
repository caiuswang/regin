"""A parked call has to reach the operator out-of-band, not just the trace.

Approve-from-phone is only a feature if the phone rings: the hook tier pushes
`permission.pending` for a session the user drives, so before this a session
regin *owned* was the one that waited silently until `park_timeout_sec`
declined it. These tests pin the push, the dismissal, and — the part that is
easy to get wrong — that the session-keyed card is not retired while other
calls in the same session are still parked.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import policy, registry, runner as runner_mod
from lib.settings import settings


@pytest.fixture
def notices(monkeypatch):
    """Capture what would have gone to the inbox / push channels."""
    from lib.agent_messages import event_notify

    pushed: list[dict] = []
    dismissed: list[str] = []
    monkeypatch.setattr(event_notify, "notify_permission_request",
                        lambda *, trace_id, attrs:
                        pushed.append({"trace_id": trace_id, **attrs}) or True)
    monkeypatch.setattr(event_notify, "resolve_permission",
                        lambda trace_id: dismissed.append(trace_id))
    return {"pushed": pushed, "dismissed": dismissed}


class _Ctx:
    def __init__(self, tool_use_id="tu-1"):
        self.tool_use_id = tool_use_id


QUESTION_INPUT = {
    "questions": [{
        "question": "Ship it?",
        "header": "Ship",
        "options": [{"label": "yes"}, {"label": "no"}],
    }],
}


def _runner(trace_id="sdk-park"):
    return runner_mod.AgentRunner(trace_id)


def test_a_parked_question_pushes_the_question_itself(notices):
    run = _runner()

    asyncio.run(run._notify_park("question", "AskUserQuestion",
                                 QUESTION_INPUT, "tu-1"))

    assert len(notices["pushed"]) == 1
    pushed = notices["pushed"][0]
    assert pushed["trace_id"] == "sdk-park"
    # The card formats off `questions`, so shipping only the tool name would
    # push "a tool needs approval" for a question with options on screen.
    assert pushed["questions"][0]["question"] == "Ship it?"


def test_a_gated_tool_pushes_what_it_would_run(notices):
    run = _runner()

    asyncio.run(run._notify_park("tool", "Bash", {"command": "rm -rf build"},
                                 "tu-2"))

    pushed = notices["pushed"][0]
    assert pushed["tool_name"] == "Bash"
    assert "rm -rf build" in pushed["requested_permission"]


def test_a_push_failure_never_breaks_the_park(monkeypatch):
    from lib.agent_messages import event_notify

    def _boom(**_kwargs):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(event_notify, "notify_permission_request", _boom)

    # Returns normally: a park that died because a webhook was unreachable
    # would leave the agent holding a call nobody can answer.
    asyncio.run(_runner()._notify_park("tool", "Bash", {}, "tu-3"))


def test_the_card_is_dismissed_once_nothing_is_parked(notices):
    asyncio.run(_runner("sdk-d1")._dismiss_park_notice())

    assert notices["dismissed"] == ["sdk-d1"]


def test_the_card_survives_while_a_sibling_call_is_still_parked(notices):
    """One assistant message can park several calls; the card is per session."""
    async def _check():
        loop = asyncio.get_running_loop()
        ask_id = registry.register_ask(registry.PendingAsk(
            trace_id="sdk-d2", tool_use_id="tu-other", tool_input={},
            future=loop.create_future(), loop=loop, kind="tool"))
        try:
            await _runner("sdk-d2")._dismiss_park_notice()
            assert notices["dismissed"] == []
        finally:
            registry.discard_ask(ask_id)
        await _runner("sdk-d2")._dismiss_park_notice()
        assert notices["dismissed"] == ["sdk-d2"]

    asyncio.run(_check())


def test_park_pushes_then_dismisses_around_the_wait(notices, monkeypatch):
    """The whole path: parked → pushed → answered → card retired."""
    # The answer is handed back as a real `PermissionResultAllow`, so this one
    # test needs the optional `[agent-sdk]` extra the rest of the suite doesn't.
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings.agent_sdk, "park_timeout_sec", 5)
    posted: list[dict] = []
    monkeypatch.setattr(runner_mod.AgentRunner, "_post",
                        lambda self, span: _noop(posted, span))

    async def _drive():
        run = _runner("sdk-p1")
        run.loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            run._park("question", "AskUserQuestion", QUESTION_INPUT, _Ctx()))
        for _ in range(200):
            if registry.pending_asks("sdk-p1"):
                break
            await asyncio.sleep(0)
        assert notices["pushed"], "the operator was never told"
        assert notices["dismissed"] == [], "dismissed before it was answered"
        ok, _detail = registry.resolve_ask("sdk-p1", [{"option_index": 0}])
        assert ok
        return await task

    result = asyncio.run(_drive())

    assert notices["dismissed"] == ["sdk-p1"]
    # The answer still round-trips: notifying must not change what the tool got.
    assert result.updated_input["answers"] == {"Ship it?": "yes"}


async def _noop(posted, span):
    if span:
        posted.append(span)


def test_teardown_retires_a_card_a_cancelled_park_could_not(notices,
                                                            monkeypatch):
    """A park cancelled with its session never runs its own dismissal."""
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: _FakeClient())
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(runner_mod.AgentRunner, "_post",
                        lambda self, span: _noop([], span))

    async def _start_and_die():
        run = runner_mod.AgentRunner("sdk-teardown")
        await run.start()
        await run.stop()

    asyncio.run(_start_and_die())

    assert notices["dismissed"] == ["sdk-teardown"]


# ── resume ───────────────────────────────────────────────────────────

def test_a_resumed_run_names_the_session_it_continues(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: seen.update(kw) or _FakeClient())
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    spans: list[dict] = []
    monkeypatch.setattr(runner_mod.AgentRunner, "_post",
                        lambda self, span: _noop(spans, span))

    async def _start():
        run = runner_mod.AgentRunner("sdk-r1", resume="old-session-42")
        await run.start()
        await run.stop()

    asyncio.run(_start())

    # Reaches the SDK, so the conversation is actually continued …
    assert seen["resume"] == "old-session-42"
    start = next(s for s in spans if s["name"] == "session.start")
    # … and is visible in the trace, which is otherwise a session that opens
    # mid-conversation with no explanation.
    assert start["attributes"]["resumed_from"] == "old-session-42"


def test_a_normal_run_carries_no_resume_marker(monkeypatch):
    monkeypatch.setattr(runner_mod.client, "new_client",
                        lambda **kw: _FakeClient())
    monkeypatch.setattr(runner_mod.store, "upsert_run", lambda *a, **kw: None)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    spans: list[dict] = []
    monkeypatch.setattr(runner_mod.AgentRunner, "_post",
                        lambda self, span: _noop(spans, span))

    async def _start():
        run = runner_mod.AgentRunner("sdk-r2")
        await run.start()
        await run.stop()

    asyncio.run(_start())

    start = next(s for s in spans if s["name"] == "session.start")
    assert "resumed_from" not in start["attributes"]


class _FakeClient:
    async def connect(self):
        return None

    async def disconnect(self):
        return None


# ── the shadowed-gating report ───────────────────────────────────────

def test_a_per_run_mode_that_shadows_gating_is_reported(monkeypatch):
    """`RunOptions.permission_mode` wins over the config, so a report keyed on
    the config alone would miss the mode the operator actually chose."""
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", True)
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "default")

    assert settings.agent_sdk.shadowed_gating() == ""
    assert "acceptEdits" in settings.agent_sdk.shadowed_gating("acceptEdits")


def test_nothing_gated_means_nothing_to_shadow(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", False)
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", [])

    assert settings.agent_sdk.shadowed_gating("bypassPermissions") == ""


def test_notify_attrs_keep_the_hook_tier_vocabulary():
    """One inbox card shape has to render a park from either producer."""
    attrs = policy.notify_attrs("plan", "ExitPlanMode", {"plan": "do it"},
                                "tu-9")

    assert attrs["tool_name"] == "ExitPlanMode"
    assert attrs["tool_use_id"] == "tu-9"
    assert attrs["requested_permission"]
