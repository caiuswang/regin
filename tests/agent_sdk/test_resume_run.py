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
    AssistantMessage, ResultMessage, TextBlock, captured,
)

_TRACE = "sdk-resume01"
# What the child `claude` calls itself — the id `--resume` is given, and the
# only id that makes `resumed_from` legible.
_CHILD = "11111111-2222-3333-4444-555555555555"


@dataclass
class SystemInit:
    """The first message of a session, the one that names it."""

    session_id: str


class NamedClient:
    """A fake SDK client whose session announces its own id."""

    def __init__(self, session_id: str = _CHILD):
        self.session_id = session_id
        self.prompts: list[str] = []
        self.resume: str | None = None
        self.connects = 0

    async def connect(self):
        self.connects += 1

    async def query(self, text):
        self.prompts.append(text)

    async def receive_response(self):
        yield SystemInit(session_id=self.session_id)
        yield AssistantMessage(content=[TextBlock("done")])
        yield ResultMessage()

    async def interrupt(self):
        pass

    async def disconnect(self):
        pass


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
