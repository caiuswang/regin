"""Resuming a stopped run regin launched (`lib/agent_sdk`, `agent_runs` route).

Stopping used to be terminal: the client disconnected, the child `claude` died,
and the only way forward was a brand-new run under a brand-new trace — so one
conversation showed up on `/live` as two unrelated sessions.

A resume is therefore continuity twice over. The CLI keeps its session
(`fork_session=False`, so the child reports the id it already had), and regin
keeps its own: the run is relaunched under the `sdk-…` trace id it already
holds, reviving that row instead of writing a second one. These tests pin both
halves, and the three states that legitimately have nothing to resume — no run,
no child ever reported, and a run that is still live.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from lib.agent_sdk import client, registry, runner as runner_mod, store, supervisor
from lib.settings import settings

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    AssistantMessage, FakeClient, ResultMessage, TextBlock, captured,
)

_TRACE = "sdk-resume01"
# What the child `claude` calls itself — the id `--resume` is given, and the
# only id that makes `resumed_from` legible.
_CHILD = "11111111-2222-3333-4444-555555555555"


@dataclass
class SystemInit:
    """The first message of a session, the one that names it."""

    session_id: str


class NamedClient(FakeClient):
    """A fake SDK client whose session announces its own id."""

    def __init__(self, session_id: str = _CHILD):
        super().__init__()
        self.session_id = session_id
        self.resume: str | None = None

    def turn_frames(self):
        return (SystemInit(session_id=self.session_id),
                AssistantMessage(content=[TextBlock("done")]),
                ResultMessage())


@pytest.fixture
def sdk_clients(monkeypatch):
    """Hand every runner a fresh fake client, recording what it resumed."""
    made: list[NamedClient] = []

    def _new_client(**kwargs):
        fake = NamedClient()
        fake.resume = kwargs.get("resume")
        made.append(fake)
        return fake

    monkeypatch.setattr(runner_mod.client, "new_client", _new_client)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    return made


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


async def _one_run(trace_id, *, resume=None):
    run = runner_mod.AgentRunner(trace_id, resume=resume)
    run.enqueue("go", waiting=False)
    run.close()
    await run.start()
    await run.pump()
    await run.stop()
    return run


def _spans(captured, name):
    return [s for s in captured["spans"] if s["name"] == name]


# ── the run row ───────────────────────────────────────────────────────

def test_a_resumed_run_revives_its_row_rather_than_adding_one(
        flask_client, captured, sdk_clients):
    """One conversation, one `agent_runs` row — the id is reused, so the row
    the operator stopped is the row that comes back."""
    asyncio.run(_one_run(_TRACE))
    stopped = store.get_run(_TRACE)

    asyncio.run(_one_run(_TRACE, resume=_CHILD))
    resumed = store.get_run(_TRACE)

    assert stopped["status"] == "exited"
    assert resumed["trace_id"] == _TRACE
    assert resumed["cli_session_id"] == _CHILD == stopped["cli_session_id"]
    # `upsert_run` is keyed on the trace id, so a revived run cannot fork the
    # row; assert it anyway — a second row is the failure this design avoids.
    from lib.orm import SessionLocal
    from lib.orm.models import AgentRun
    from sqlmodel import select
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentRun).where(AgentRun.trace_id == _TRACE)).all()
    assert len(rows) == 1


def test_the_run_is_running_again_while_the_resumed_session_is_up(
        flask_client, captured, sdk_clients):
    asyncio.run(_one_run(_TRACE))
    seen = {}

    async def scenario():
        run = runner_mod.AgentRunner(_TRACE, resume=_CHILD)
        await run.start()
        seen["status"] = store.get_run(_TRACE)["status"]
        seen["owned"] = registry.is_sdk_owned(_TRACE)
        await run.stop()

    asyncio.run(scenario())

    assert seen["status"] == "running"
    assert seen["owned"] is True


def test_the_second_session_start_names_the_child_not_itself(
        flask_client, captured, sdk_clients):
    """`resumed_from` pointing at the trace it is already on would tell a later
    reader nothing; the CLI session it continues is the fact worth recording."""
    asyncio.run(_one_run(_TRACE))
    asyncio.run(_one_run(_TRACE, resume=_CHILD))

    starts = _spans(captured, "session.start")
    assert len(starts) == 2
    assert not starts[0]["attributes"].get("resumed_from")
    assert starts[1]["attributes"]["resumed_from"] == _CHILD
    assert starts[1]["trace_id"] == _TRACE


def test_the_child_keeps_its_session_id_across_the_resume(
        flask_client, captured, sdk_clients):
    """The alias the trace group is built on survives, so both halves keep
    resolving to one session."""
    asyncio.run(_one_run(_TRACE))
    asyncio.run(_one_run(_TRACE, resume=_CHILD))

    assert [c.resume for c in sdk_clients] == [None, _CHILD]
    assert store.get_run(_TRACE)["cli_session_id"] == _CHILD


# ── the alias is not silently re-pointed ──────────────────────────────

def test_set_cli_session_refuses_to_re_point_an_established_alias(flask_client):
    """A second, different child id means the CLI forked despite
    `fork_session=False` (or two runs claimed one row). Overwriting would
    orphan every span the first half wrote."""
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)

    store.set_cli_session(_TRACE, "99999999-0000-0000-0000-000000000000")

    assert store.get_run(_TRACE)["cli_session_id"] == _CHILD


def test_the_first_write_and_a_repeat_still_go_through(flask_client):
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    assert store.get_run(_TRACE)["cli_session_id"] == _CHILD

    # Called for every message of every turn — a repeat must be a no-op, not a
    # refusal that logs on each one.
    store.set_cli_session(_TRACE, _CHILD)
    assert store.get_run(_TRACE)["cli_session_id"] == _CHILD


def test_a_forked_session_is_never_asked_for(flask_client, monkeypatch,
                                             tmp_path):
    """Forking would hand the resumed run a NEW child id, stranding the trace
    its first half was recorded under — so regin states its own default rather
    than inheriting whatever the SDK's is, here a flipped one."""
    import claude_agent_sdk

    cli = tmp_path / "claude"
    cli.write_text("")
    monkeypatch.setattr(settings.agent_sdk, "cli_path", str(cli))
    real = claude_agent_sdk.ClaudeAgentOptions
    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions",
                        lambda **kw: real(**{"fork_session": True, **kw}))

    built = client.build_options(resume=_CHILD)

    assert built.resume == _CHILD
    assert built.fork_session is False


# ── the launch route ──────────────────────────────────────────────────

@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


@pytest.fixture
def stub_launch(monkeypatch):
    seen = {}

    def _launch(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return kwargs.get("trace_id") or "sdk-fresh0001"

    monkeypatch.setattr(supervisor, "launch", _launch)
    return seen


def test_resuming_a_run_reopens_its_child_under_the_same_trace(
        flask_client, enabled, stub_launch):
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    body = flask_client.post("/api/agent-runs",
                             json={"prompt": "carry on", "resume": _TRACE})

    assert body.status_code == 200
    # The CLI is handed the child's id — it has no session under `sdk-…`.
    assert stub_launch["resume"] == _CHILD
    assert stub_launch["trace_id"] == _TRACE
    assert body.get_json()["trace_id"] == _TRACE


def test_a_run_whose_child_never_reported_is_refused(flask_client, enabled,
                                                     stub_launch):
    """`cli_session_id IS NULL` — the child died before naming itself, so there
    is genuinely no conversation to continue."""
    store.upsert_run(_TRACE, status="exited")

    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "carry on", "resume": _TRACE})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_an_sdk_id_regin_has_no_run_for_is_refused(flask_client, enabled,
                                                   stub_launch):
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "carry on", "resume": "sdk-nothing"})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_a_live_run_is_not_resumed_out_from_under_itself(flask_client, enabled,
                                                         stub_launch):
    """Reusing the trace id of a run this process still owns would replace it
    in the registry — its child left running with nothing able to stop it."""
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    registry.register_run(_TRACE, object())
    try:
        res = flask_client.post("/api/agent-runs",
                                json={"prompt": "carry on", "resume": _TRACE})
    finally:
        registry.unregister_run(_TRACE)

    assert res.status_code == 400
    # A registered run *can* be stopped, so this one keeps the actionable
    # advice its starting twin cannot honestly give.
    assert "stop it before resuming" in res.get_json()["error"]
    assert "prompt" not in stub_launch


def test_a_resume_arriving_while_the_run_is_still_starting_is_refused(
        flask_client, enabled, stub_launch):
    """The window the liveness check used to miss: the first resume has been
    scheduled, but its runner registers only after `connect()` has spawned the
    child. Admitting the second puts two runners on one trace id."""
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)
    registry.reserve_run(_TRACE)
    try:
        res = flask_client.post("/api/agent-runs",
                                json={"prompt": "carry on", "resume": _TRACE})
    finally:
        registry.release_run(_TRACE)

    assert res.status_code == 400
    # Not "stop it before resuming": a run that has not registered yet cannot
    # be stopped, so that advice would send the operator to a dead control.
    assert "starting" in res.get_json()["error"]
    assert "prompt" not in stub_launch


def test_a_resume_by_child_id_is_refused_while_the_run_is_starting(
        flask_client, enabled, stub_launch):
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)
    registry.reserve_run(_TRACE)
    try:
        res = flask_client.post("/api/agent-runs",
                                json={"prompt": "carry on", "resume": _CHILD})
    finally:
        registry.release_run(_TRACE)

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_a_starting_run_reports_itself_as_owned_and_not_resumable(flask_client):
    """`resumable` is what the launch sheet offers, so it has to go false the
    moment the run is claimed — not when its child finally exists."""
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    registry.reserve_run(_TRACE)
    try:
        body = flask_client.get(f"/api/agent-runs/{_TRACE}").get_json()
    finally:
        registry.release_run(_TRACE)

    assert body["owned"] is True
    assert body["resumable"] is False


# ── a resume with nothing to say ──────────────────────────────────────

def test_a_resume_needs_no_prompt(flask_client, enabled, stub_launch):
    """Reopening the session *is* the act. Demanding a first turn to get there
    would make an operator invent one, and the card they land on already has a
    composer for the turn they actually want."""
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    res = flask_client.post("/api/agent-runs", json={"resume": _TRACE})

    assert res.status_code == 200
    assert stub_launch["prompt"] == ""
    assert stub_launch["resume"] == _CHILD


def test_a_fresh_run_needs_no_prompt_either(flask_client, enabled, stub_launch):
    """The same relaxation covers a fresh run: it comes up waiting on its
    composer rather than being refused for having nothing to say yet."""
    res = flask_client.post("/api/agent-runs", json={})

    assert res.status_code == 200
    assert stub_launch["prompt"] == ""
    assert stub_launch["resume"] is None


def test_a_one_shot_resume_still_needs_a_prompt(flask_client, enabled,
                                                stub_launch):
    """`one_shot` ends the session with its first turn; with no turn to run it
    would connect and immediately disconnect, which is never what was meant."""
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    res = flask_client.post("/api/agent-runs",
                            json={"resume": _TRACE, "one_shot": True})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_a_promptless_resume_runs_no_turn_and_waits(flask_client, captured,
                                                    sdk_clients):
    """The session comes up and stops there: the conversation is reopened, and
    the next turn arrives from the composer rather than from the launch."""
    async def _reopen():
        run = runner_mod.AgentRunner(_TRACE, resume=_CHILD)
        run.close()  # nothing queued; the terminator is all the pump will see
        await run.start()
        await run.pump()
        await run.stop()
        return run

    asyncio.run(_reopen())

    assert sdk_clients[0].connects == 1
    assert sdk_clients[0].prompts == []
    assert sdk_clients[0].resume == _CHILD


def test_a_session_its_terminal_is_still_driving_is_refused(
        flask_client, enabled, stub_launch, monkeypatch):
    """The picker hides these, but the route is the authority: a direct POST —
    or a list that went stale between render and pick — would otherwise put a
    second process on one session id."""
    from lib.agent_bridge import delivery

    monkeypatch.setattr(delivery, "session_is_live", lambda tid: tid == "abc-123")

    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "carry on", "resume": "abc-123"})

    assert res.status_code == 400
    assert "live in a terminal" in res.get_json()["error"]
    assert "prompt" not in stub_launch


def test_a_terminal_session_still_resumes_into_a_fresh_trace(flask_client,
                                                             enabled,
                                                             stub_launch):
    """A session the user drove has no `agent_runs` row: its trace id IS the
    CLI's session id, and continuing it is a new run."""
    flask_client.post("/api/agent-runs",
                      json={"prompt": "carry on", "resume": "abc-123"})

    assert stub_launch["resume"] == "abc-123"
    assert stub_launch["trace_id"] is None


# ── resumed by the id the operator actually holds ─────────────────────

def test_resuming_by_the_child_id_reopens_the_run_it_belongs_to(
        flask_client, enabled, stub_launch):
    """The child's id is the canonical one — the session list hides the
    `sdk-…` half — so it is the id `/live` links to and the id a resume
    arrives with."""
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    body = flask_client.post("/api/agent-runs",
                             json={"prompt": "carry on", "resume": _CHILD})

    assert body.status_code == 200
    assert stub_launch["resume"] == _CHILD
    assert stub_launch["trace_id"] == _TRACE
    assert body.get_json()["trace_id"] == _TRACE


def test_resuming_by_the_child_id_leaves_one_run_claiming_that_child(
        flask_client, captured, sdk_clients, enabled, monkeypatch):
    """The failure this closes: a second `agent_runs` row on one child makes
    the resumed half unreachable, because `trace_group` resolves the child to
    exactly one run."""
    asyncio.run(_one_run(_TRACE))

    def _launch(prompt, **kwargs):
        trace_id = kwargs.get("trace_id") or "sdk-fresh0001"
        asyncio.run(_one_run(trace_id, resume=kwargs.get("resume")))
        return trace_id

    monkeypatch.setattr(supervisor, "launch", _launch)
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "carry on", "resume": _CHILD})

    assert res.get_json()["trace_id"] == _TRACE
    from lib.orm import SessionLocal
    from lib.orm.models import AgentRun
    from sqlmodel import select
    with SessionLocal() as session:
        claiming = session.exec(
            select(AgentRun.trace_id)
            .where(AgentRun.cli_session_id == _CHILD)).all()
    assert claiming == [_TRACE]


def test_a_live_run_is_not_resumed_out_from_under_itself_by_child_id(
        flask_client, enabled, stub_launch):
    """Liveness is a property of the run, not of the id it was named by."""
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    registry.register_run(_TRACE, object())
    try:
        res = flask_client.post("/api/agent-runs",
                                json={"prompt": "carry on", "resume": _CHILD})
    finally:
        registry.unregister_run(_TRACE)

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_the_ambiguous_child_resolves_the_way_the_trace_view_does(flask_client):
    """`cli_session_id` is not unique and duplicate rows predate the alias, so
    resume and the trace group must pick the same one — resuming into a trace
    the reader does not consider canonical would render nothing."""
    from lib.orm.engine import get_connection
    from lib.trace.alias import trace_group

    for trace_id in ("sdk-zzz9", "sdk-aaa1"):
        store.upsert_run(trace_id, status="exited")
        store.set_cli_session(trace_id, _CHILD)

    conn = get_connection()
    try:
        canonical = trace_group(conn, _CHILD)
    finally:
        conn.close()

    assert store.find_run(_CHILD)["trace_id"] == canonical[1] == "sdk-aaa1"


def test_an_unmigrated_db_says_no_run_instead_of_failing_the_launch(
        flask_client, enabled, stub_launch):
    """Booting the server does not migrate, so a DB without `cli_session_id`
    is reachable. It cannot know about aliases — but it can still reopen a
    session the user drove, which is what it could do before runs were merged.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP INDEX idx_agent_runs_cli_session_id")
        conn.execute("ALTER TABLE agent_runs DROP COLUMN cli_session_id")
        conn.commit()
    finally:
        conn.close()

    assert store.find_run(_CHILD) is None
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "carry on", "resume": "abc-123"})

    assert res.status_code == 200
    assert stub_launch["resume"] == "abc-123"
    assert stub_launch["trace_id"] is None


def test_an_unrecorded_id_resolves_to_no_run_at_all(flask_client):
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    assert store.find_run("abc-123") is None
    assert store.find_run("") is None


# ── what the status route reports ─────────────────────────────────────

def test_status_reports_resumability(flask_client):
    store.upsert_run(_TRACE, status="exited")
    unlinked = flask_client.get(f"/api/agent-runs/{_TRACE}").get_json()

    store.set_cli_session(_TRACE, _CHILD)
    linked = flask_client.get(f"/api/agent-runs/{_TRACE}").get_json()

    assert unlinked["cli_session_id"] is None
    assert unlinked["resumable"] is False
    assert linked["cli_session_id"] == _CHILD
    assert linked["resumable"] is True


def test_status_answers_for_the_child_id_and_reports_the_run_trace(
        flask_client):
    """`/live/<child-uuid>` is where the session list sends an operator, so a
    404 there would offer them a resume the server then refuses to honour."""
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    body = flask_client.get(f"/api/agent-runs/{_CHILD}").get_json()

    assert body["trace_id"] == _TRACE
    assert body["cli_session_id"] == _CHILD
    assert body["resumable"] is True


def test_status_by_child_id_reports_the_runs_liveness_not_the_ids(flask_client):
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    registry.register_run(_TRACE, object())
    try:
        body = flask_client.get(f"/api/agent-runs/{_CHILD}").get_json()
    finally:
        registry.unregister_run(_TRACE)

    assert body["owned"] is True
    assert body["resumable"] is False


def test_a_session_regin_never_launched_is_still_a_404(flask_client):
    store.upsert_run(_TRACE, status="exited")
    store.set_cli_session(_TRACE, _CHILD)

    assert flask_client.get("/api/agent-runs/abc-123").status_code == 404


def test_a_live_run_reports_itself_as_not_resumable(flask_client):
    store.upsert_run(_TRACE, status="running")
    store.set_cli_session(_TRACE, _CHILD)
    registry.register_run(_TRACE, object())
    try:
        body = flask_client.get(f"/api/agent-runs/{_TRACE}").get_json()
    finally:
        registry.unregister_run(_TRACE)

    assert body["owned"] is True
    assert body["resumable"] is False


# ── what a revived row says about itself ──────────────────────────────

def test_a_revived_row_stops_reporting_why_its_last_life_ended(flask_client):
    """`detail` explains an outcome. A run that is `running` again has none,
    and a poller reading `running` + "stopped" gets a contradiction."""
    store.upsert_run(_TRACE, status="exited", detail="stopped")

    store.upsert_run(_TRACE, status="running")

    assert store.get_run(_TRACE)["detail"] is None


def test_a_run_resumed_through_the_runner_comes_back_without_a_detail(
        flask_client, captured, sdk_clients):
    seen = {}

    async def scenario():
        run = runner_mod.AgentRunner(_TRACE, resume=_CHILD)
        await run.start()
        seen.update(store.get_run(_TRACE))
        await run.stop()

    asyncio.run(_one_run(_TRACE))
    asyncio.run(scenario())

    assert store.get_run(_TRACE)["detail"]  # the stop wrote one
    assert seen["status"] == "running"
    assert seen["detail"] is None


def test_an_outcome_detail_survives_a_later_write_of_the_same_status(
        flask_client):
    """Only the return to `running` clears it — a second `exited` write with
    nothing to say must not erase the reason the run ended."""
    store.upsert_run(_TRACE, status="exited", detail="idle timeout")

    store.upsert_run(_TRACE, status="exited", pid=4242)

    assert store.get_run(_TRACE)["detail"] == "idle timeout"


# ── the supervisor ────────────────────────────────────────────────────

def test_launch_run_reuses_a_supplied_trace_id_instead_of_minting_one(
        tmp_db, monkeypatch):
    seen = {}

    async def _run_session(trace_id, prompt, **kwargs):
        seen["trace_id"] = trace_id
        seen["resume"] = kwargs.get("resume")

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    handle = supervisor.launch_run("carry on", trace_id=_TRACE, resume=_CHILD)
    handle.wait(timeout=10)

    assert handle.trace_id == _TRACE
    assert seen == {"trace_id": _TRACE, "resume": _CHILD}


def test_a_resume_claims_its_session_before_the_child_says_anything(
        tmp_db, monkeypatch):
    """The alias cannot wait for the first message.

    `_note_session` learns the child's id from a message, and a resume may
    carry no prompt at all — so there is no first message, and the id the
    operator's card is open on stays unowned. The id is known at launch
    (`fork_session=False`, so the child keeps it), so it is claimed here.
    """
    seen = {}

    async def _run_session(trace_id, prompt, **kwargs):
        seen["owned"] = registry.is_sdk_owned(_CHILD)
        seen["owner"] = registry.owning_run(_CHILD)

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD).wait(timeout=10)

    assert seen == {"owned": True, "owner": _TRACE}


def test_the_claim_is_given_back_when_the_run_ends(tmp_db, monkeypatch):
    """An alias outliving its run would keep pointing that session at an id
    nothing holds — steering it would then reach neither the run nor the
    pane the session is actually sitting in."""
    async def _run_session(trace_id, prompt, **kwargs):
        pass

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD).wait(timeout=10)

    assert registry.owning_run(_CHILD) == _CHILD
    assert registry.is_sdk_owned(_CHILD) is False


def test_a_promptless_resume_steers_the_run_not_the_pane_it_came_from(
        flask_client, monkeypatch):
    """The reported bug, end to end.

    A session driven in a terminal leaves a `bridge_panes` row behind. Resume
    it with no prompt and — until the alias is claimed at launch — the card is
    open on an id the SDK does not own, so the operator's first prompt is typed
    into that pane instead. The pane is long since occupied by whatever session
    started there next, which is what received the `/exit`.
    """
    import threading

    from lib.agent_bridge import delivery, store as bridge_store
    from lib.orm.engine import get_connection
    from web.blueprints import bridge as bridge_bp

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, tmux_socket, reachable, cwd) "
            "VALUES (?, '%0', 111, 222, NULL, 1, '/work')", (_CHILD,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)

    typed: list[tuple[str, str]] = []
    queued: list[tuple[str, str]] = []

    def _deliver(trace_id, text):
        typed.append((trace_id, text))
        return delivery.DeliveryResult(True, "delivered to %0")

    def _submit(trace_id, text):
        queued.append((trace_id, text))
        return True, "prompt queued"

    monkeypatch.setattr(delivery, "deliver", _deliver)
    monkeypatch.setattr(bridge_bp.agent_sdk, "submit_prompt", _submit)

    running, release = threading.Event(), threading.Event()

    async def _run_session(trace_id, prompt, **kwargs):
        running.set()
        await asyncio.get_running_loop().run_in_executor(None, release.wait)

    monkeypatch.setattr(supervisor, "run_session", _run_session)
    handle = supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD)
    assert running.wait(timeout=10)
    try:
        res = flask_client.post(f"/api/sessions/{_CHILD}/bridge-send",
                                json={"text": "/exit"})
    finally:
        release.set()
        handle.wait(timeout=10)

    assert res.status_code == 200
    # The route hands the id it was called with straight through; the registry
    # resolves the alias to the run.
    assert queued == [(_CHILD, "/exit")]
    assert typed == []
    # The pane row is real and still resolves — routing, not reachability, is
    # what keeps the keystrokes out of it.
    assert bridge_store.get_reachable_pane(_CHILD) is not None


def test_an_ordinary_launch_still_mints_its_own_trace_id(tmp_db, monkeypatch):
    seen = {}

    async def _run_session(trace_id, prompt, **kwargs):
        seen["trace_id"] = trace_id

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    handle = supervisor.launch_run("start fresh")
    handle.wait(timeout=10)

    assert seen["trace_id"].startswith("sdk-")
    assert seen["trace_id"] != _TRACE


# ── the trace row /live polls ─────────────────────────────────────────
# Reviving the `agent_runs` row is not enough: the card reads the `sessions`
# row, and that one still carries the end marker of the run's previous life
# until the runner emits `session.start` — which happens only after `connect()`
# has spawned the child. `useLiveTail` stops polling for good on an ended
# session, so a card that reads the stale marker never notices the run start
# and shows no composer until the page is reloaded by hand.

_ENDED_AT = "2026-08-02T11:30:02.367710"


def _put_trace_session(trace_id: str, **overrides) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models import Session as TraceSession
    fields = dict(
        trace_id=trace_id, status="ended", ended_at=_ENDED_AT,
        ended_reason="completed", started_at="2026-08-02T11:29:55",
        last_seen=_ENDED_AT, last_start_at="2026-08-02T11:29:55",
        span_count=4, skill_reads=0, file_edits=0, rule_checks=0,
        plan_enters=0, prompts=1, tool_calls=0, is_test=0,
    )
    fields.update(overrides)
    with SessionLocal() as session:
        session.add(TraceSession(**fields))
        session.commit()


def _settle(predicate, timeout: float = 5.0) -> bool:
    """Poll `predicate` until it holds. For state a done-callback writes: the
    future releases its waiters before it invokes them."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _trace_session(trace_id: str):
    from sqlmodel import select as _select

    from lib.orm import SessionLocal
    from lib.orm.models import Session as TraceSession
    with SessionLocal() as session:
        return session.exec(
            _select(TraceSession).where(
                TraceSession.trace_id == trace_id)).first()


def test_reviving_clears_the_end_marker_the_card_stops_polling_on(tmp_db):
    _put_trace_session(_TRACE)

    store.revive_trace_session(_TRACE)

    row = _trace_session(_TRACE)
    assert (row.status, row.ended_at, row.ended_reason) == ("active", None, None)


def test_reviving_covers_the_child_row_a_trace_read_resolves_to(tmp_db):
    """`alias.trace_group` makes the CHILD's id canonical, so the `sdk-…` row
    is not the one `/live` reads. Reviving it alone leaves both entry points
    still looking at an ended session."""
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)

    store.revive_trace_session(_TRACE, _CHILD)

    for trace_id in (_TRACE, _CHILD):
        row = _trace_session(trace_id)
        assert (row.status, row.ended_at) == ("active", None), trace_id


def test_reviving_leaves_a_session_that_never_claimed_to_end(tmp_db):
    """A blank status is not an end marker. Stamping one 'active' would invent
    liveness the launch has no evidence for."""
    _put_trace_session("never-ended", status=None, ended_at=None,
                       ended_reason=None)

    store.revive_trace_session("never-ended")

    assert _trace_session("never-ended").status is None


def test_reviving_a_trace_with_no_row_yet_is_a_no_op(tmp_db):
    """A session reopened by id runs under a brand-new trace, so there is
    nothing to revive — and nothing to create either: the row is the ingest's
    to write, and a stub here would show an empty session in every listing."""
    store.revive_trace_session("sdk-nosuchrow")

    assert _trace_session("sdk-nosuchrow") is None


def test_a_resumed_launch_revives_the_row_before_the_route_answers(
        tmp_db, monkeypatch):
    """Synchronously, not from the runner: the client navigates to `/live` the
    instant the launch returns, and its first read decides whether the card
    keeps polling at all."""
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)
    seen = {}

    async def _run_session(trace_id, prompt, **kwargs):
        seen["ran"] = True

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD)

    assert (_trace_session(_TRACE).status, _trace_session(_TRACE).ended_at) \
        == ("active", None)
    assert (_trace_session(_CHILD).status, _trace_session(_CHILD).ended_at) \
        == ("active", None)


def test_a_launch_that_dies_before_connecting_puts_the_end_marker_back(
        tmp_db, monkeypatch):
    """The revive is optimistic — it declares the session live before the child
    exists. A spawn that fails writes no `session.end` (there is no session to
    close), so without this the row stays 'active' forever and every listing
    shows a green session nobody can reach."""
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)

    def _explode(**kwargs):
        raise runner_mod.client.ClaudeCliNotFound("no claude on PATH")

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(runner_mod.client, "new_client", _explode)

    supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD).wait(timeout=10)

    # The restore rides the future's done-callback, which `concurrent.futures`
    # invokes *after* it releases waiters — so `wait()` returning is not the
    # signal, the row is.
    for trace_id in (_TRACE, _CHILD):
        marker = _settle(lambda: _trace_session(trace_id).ended_at is not None)
        assert marker, trace_id
        row = _trace_session(trace_id)
        assert (row.status, row.ended_at, row.ended_reason) \
            == ("ended", _ENDED_AT, "completed"), trace_id


def test_a_run_that_really_ran_keeps_its_own_end_marker(tmp_db, monkeypatch):
    """The restore must never overwrite a fresher verdict: a run that connected
    wrote its own `session.end`, and that is the truth about this life of the
    session, not the marker the launch cleared."""
    _put_trace_session(_TRACE)
    revived = store.revive_trace_session(_TRACE)
    # What the ingest does when the resumed run finally ends.
    with_new_end = "2026-08-02T15:00:00"
    row = _trace_session(_TRACE)
    from lib.orm import SessionLocal
    with SessionLocal() as session:
        live = session.get(type(row), _TRACE)
        live.status, live.ended_at, live.ended_reason = (
            "ended", with_new_end, "stopped")
        session.commit()

    store.restore_trace_session(revived)

    assert _trace_session(_TRACE).ended_at == with_new_end


def test_a_fresh_launch_leaves_other_sessions_alone(tmp_db, monkeypatch):
    """Only the trace being relaunched is touched — a minted id has no row, and
    an unrelated ended session must not be resurrected by someone else's
    launch."""
    _put_trace_session("some-other-session")

    async def _run_session(trace_id, prompt, **kwargs):
        pass

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    supervisor.launch_run("start fresh").wait(timeout=10)

    other = _trace_session("some-other-session")
    assert (other.status, other.ended_at) == ("ended", _ENDED_AT)


def test_the_card_reads_a_live_session_the_moment_the_launch_returns(
        flask_client, monkeypatch):
    """End to end over the surface the operator actually drives: launch a
    resume, then read the same shallow map `/live` polls. An `ended` verdict
    here is what killed the poll loop and hid the composer until a reload."""
    # Both halves, the way every real resumable run is stored: the child's row
    # is the canonical one a trace read resolves the group to.
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)
    store.upsert_run(_TRACE, status="exited", cwd="/tmp")
    store.set_cli_session(_TRACE, _CHILD)

    # Held open across the read: a run that had already finished would hand
    # back its reservation, and the composer's other gate would read False for
    # a reason this test is not about.
    import threading
    running, release = threading.Event(), threading.Event()

    async def _run_session(trace_id, prompt, **kwargs):
        running.set()
        await asyncio.get_running_loop().run_in_executor(None, release.wait)

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "run_session", _run_session)

    launched = flask_client.post("/api/agent-runs", json={"resume": _CHILD})
    assert launched.get_json()["trace_id"] == _TRACE
    assert running.wait(timeout=10)
    try:
        # Both entry points, because the card may be open on either: the run's
        # own id, or the child's (which is where `onLaunched` stays when the
        # resume continues the session already in view).
        cards = {tid: flask_client.get(
            f"/api/sessions/{tid}/map?shallow=1&limit=50").get_json()
            for tid in (_TRACE, _CHILD)}
    finally:
        release.set()
    for tid, card in cards.items():
        assert card["status"] == "active", tid
        assert card["ended_at"] is None, tid
        assert card["phase"] != "ended", tid
        # The composer needs both halves: a session that is not over, and a
        # channel regin can steer it through.
        assert card["bridge_reachable"] is True, tid


def test_the_undo_stands_down_once_the_id_belongs_to_another_run(tmp_db):
    """A stopped run can be resumed AGAIN while the previous one is still
    tearing down, so the second launch finds the row already live and captures
    nothing to restore. An unconditional undo from the first would then stamp a
    running session 'ended' — killing its card's poll loop, which is the bug
    this whole change exists to fix."""
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)
    revived = store.revive_trace_session(_TRACE, _CHILD)
    assert len(revived) == 2

    token = object()
    assert registry.reserve_run(_TRACE, token)
    try:
        supervisor._undo_revive(_TRACE, revived)
    finally:
        registry.release_run(_TRACE, token)

    for trace_id in (_TRACE, _CHILD):
        assert _trace_session(trace_id).ended_at is None, trace_id


def test_the_undo_runs_once_nobody_holds_the_id(tmp_db):
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)
    revived = store.revive_trace_session(_TRACE, _CHILD)

    supervisor._undo_revive(_TRACE, revived)

    for trace_id in (_TRACE, _CHILD):
        assert _trace_session(trace_id).ended_at == _ENDED_AT, trace_id


def test_a_failing_on_done_still_puts_the_end_marker_back(tmp_db, monkeypatch):
    """The undo runs in a `finally`: a raise from the reservation half must not
    be the reason a session is left claiming to be live forever."""
    _put_trace_session(_TRACE)
    _put_trace_session(_CHILD)

    def _boom(*a, **kw):
        registry.release_run(a[0], a[1])
        raise RuntimeError("callback blew up")

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "_on_done", _boom)

    async def _run_session(trace_id, prompt, **kwargs):
        raise runner_mod.client.ClaudeCliNotFound("no claude on PATH")

    monkeypatch.setattr(supervisor, "run_session", _run_session)
    supervisor.launch_run("", trace_id=_TRACE, resume=_CHILD).wait(timeout=10)

    for trace_id in (_TRACE, _CHILD):
        assert _settle(
            lambda: _trace_session(trace_id).ended_at == _ENDED_AT), trace_id
