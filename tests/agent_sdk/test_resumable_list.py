"""What the launch sheet may offer to continue (`lib/agent_sdk/resumable.py`).

The picker's whole value is that everything in it can actually be resumed. A
trace row exists for every session regin ever saw, but `claude --resume` needs
the provider's transcript still on disk and the launch route refuses a run it is
still holding — so the list is defined by those exclusions, and these tests pin
each one. The rest pin what an operator needs *per row* to act on it: which id,
which cwd, and whether picking it continues the trace or opens a new one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.agent_sdk import registry, resumable, store

_ON_DISK = "aaaaaaaa-0000-0000-0000-000000000001"
_LAUNCHED = "bbbbbbbb-0000-0000-0000-000000000002"
_RUN_TRACE = "sdk-listrun01"


def _seed(trace_id: str, *, title: str = "", cwd: str = "/repo",
          last_seen: str = "2026-01-01T00:00:00") -> None:
    """One `sessions` row — the shape the picker reads."""
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as session:
        session.add(SessionModel(trace_id=trace_id, title=title, cwd=cwd,
                                 started_at=last_seen, last_seen=last_seen,
                                 is_test=0))
        session.commit()


@pytest.fixture
def provider(monkeypatch):
    """The provider facts the picker reads, both under test control.

    `transcripts` is which ids have a transcript still on disk; `live` is what
    the CLI reports as running. The real lookups glob the operator's
    `~/.claude/projects` and shell out to `claude agents --json`, either of
    which would make the suite depend on the machine it runs on.

    `answers_live` False makes the probe return None — the CLI could not be
    asked — which is a different fact from an empty `live` set and has to be
    steerable separately.
    """
    transcripts: set[str] = set()
    live: set[str] = set()
    state = SimpleNamespace(transcripts=transcripts, live=live,
                            answers_live=True, live_calls=0)

    class _Provider:
        provider_id = "claude"

        def find_session_transcript(self, session_id):
            return f"/t/{session_id}.jsonl" if session_id in transcripts else None

        def live_session_ids(self):
            state.live_calls += 1
            return frozenset(live) if state.answers_live else None

    # Resume resolves each session's own provider, never the active one.
    for resolver in ("provider_for_trace", "provider_for_agent_type"):
        monkeypatch.setattr(f"lib.providers.base.{resolver}",
                            lambda *a, **k: _Provider())
    return state


@pytest.fixture
def on_disk(provider):
    return provider.transcripts


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_RUN_TRACE)


def _ids(rows):
    return [r["session_id"] for r in rows]


# ── what is excluded ──────────────────────────────────────────────────

def test_a_session_without_a_transcript_is_not_offered(flask_client, on_disk):
    """regin records a session the moment a hook fires; the CLI can only reopen
    one it still has the transcript for. Offering the rest would move the
    failure from the picker to a run that dies on start."""
    _seed(_ON_DISK)
    _seed("cccccccc-0000-0000-0000-000000000003")
    on_disk.add(_ON_DISK)

    assert _ids(resumable.list_resumable()) == [_ON_DISK]


def test_the_synthetic_half_of_a_launched_run_is_never_offered(flask_client,
                                                               on_disk):
    """`sdk-<hex>` names a run row, not a CLI session — `--resume` has nothing
    to open under it."""
    _seed(_RUN_TRACE)
    on_disk.add(_RUN_TRACE)

    assert resumable.list_resumable() == []


def test_a_run_still_live_here_is_not_offered(flask_client, on_disk):
    """The launch route refuses it ("stop it before resuming"), so listing it
    would be a dead option."""
    _seed(_LAUNCHED)
    on_disk.add(_LAUNCHED)
    store.upsert_run(_RUN_TRACE, status="running")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)
    registry.register_run(_RUN_TRACE, object())

    assert resumable.list_resumable() == []


@pytest.fixture
def in_a_pane(monkeypatch):
    """Control which ids the pane registry reports as live in a terminal.

    Stubbed at `delivery.session_is_live` rather than seeded into
    `bridge_panes`: the real predicate re-reads the pane's foreground command
    over tmux, which a unit test has no business spawning."""
    live: set[str] = set()
    from lib.agent_bridge import delivery

    monkeypatch.setattr(delivery, "session_is_live", lambda tid: tid in live)
    return live


def test_a_session_a_terminal_is_still_driving_is_not_offered(
        flask_client, on_disk, in_a_pane):
    """Resuming it would put a second process on one session id — and the
    resume claims that id for the run, redirecting the live session's own
    composer at the copy."""
    _seed(_ON_DISK)
    on_disk.add(_ON_DISK)
    in_a_pane.add(_ON_DISK)

    assert resumable.list_resumable() == []


def test_the_same_session_is_offered_once_its_terminal_exits(
        flask_client, on_disk, in_a_pane):
    """The exclusion tracks the pane, not the session: nothing about the row
    changes when the CLI exits, so a stale rule would hide it forever."""
    _seed(_ON_DISK)
    on_disk.add(_ON_DISK)

    assert _ids(resumable.list_resumable()) == [_ON_DISK]


def test_the_same_run_is_offered_once_it_is_stopped(flask_client, on_disk):
    _seed(_LAUNCHED)
    on_disk.add(_LAUNCHED)
    store.upsert_run(_RUN_TRACE, status="exited")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)

    assert _ids(resumable.list_resumable()) == [_LAUNCHED]


# ── what each row tells the operator ──────────────────────────────────

def test_a_launched_run_reads_as_a_run_and_a_terminal_session_as_a_session(
        flask_client, on_disk):
    """The two resume shapes differ in what they do to the trace, so the row
    has to say which one a pick is."""
    _seed(_ON_DISK, last_seen="2026-01-01T00:00:01")
    _seed(_LAUNCHED, last_seen="2026-01-01T00:00:02")
    on_disk.update({_ON_DISK, _LAUNCHED})
    store.upsert_run(_RUN_TRACE, status="exited")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)

    kinds = {r["session_id"]: r["kind"] for r in resumable.list_resumable()}

    assert kinds == {_LAUNCHED: "run", _ON_DISK: "session"}


def test_a_launched_run_carries_the_model_it_was_held_on(flask_client, on_disk):
    """So a continuation stays on it. Dropping to the install default would
    change models mid-conversation — not something the operator asked for, and
    nothing on the card would show it happened."""
    _seed(_LAUNCHED)
    on_disk.add(_LAUNCHED)
    store.upsert_run(_RUN_TRACE, status="exited", model="claude-opus-5")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)

    assert resumable.list_resumable()[0]["model"] == "claude-opus-5"


def test_a_terminal_session_reports_no_model(flask_client, on_disk):
    """regin recorded none for a session it did not launch, and inventing one
    would pin a resume to a model the conversation may never have used."""
    _seed(_ON_DISK)
    on_disk.add(_ON_DISK)

    assert resumable.list_resumable()[0]["model"] == ""


def test_each_row_carries_the_cwd_it_ran_in(flask_client, on_disk):
    """`claude --resume` resolves the id relative to the working directory, so
    a client that picks a row without adopting its cwd resumes nothing."""
    _seed(_ON_DISK, cwd="/Users/x/worktree")
    on_disk.add(_ON_DISK)

    assert resumable.list_resumable()[0]["cwd"] == "/Users/x/worktree"


def test_newest_first(flask_client, on_disk):
    _seed(_ON_DISK, last_seen="2026-01-01T00:00:01")
    _seed(_LAUNCHED, last_seen="2026-06-01T00:00:00")
    on_disk.update({_ON_DISK, _LAUNCHED})

    assert _ids(resumable.list_resumable()) == [_LAUNCHED, _ON_DISK]


# ── search ────────────────────────────────────────────────────────────

def test_search_matches_title_id_and_cwd(flask_client, on_disk):
    """An operator hunting a past run remembers one of the three, and which one
    is not predictable."""
    _seed(_ON_DISK, title="fix the parser", cwd="/repo/alpha")
    _seed(_LAUNCHED, title="unrelated", cwd="/repo/beta")
    on_disk.update({_ON_DISK, _LAUNCHED})

    assert _ids(resumable.list_resumable("parser")) == [_ON_DISK]
    assert _ids(resumable.list_resumable("alpha")) == [_ON_DISK]
    assert _ids(resumable.list_resumable(_LAUNCHED[:8])) == [_LAUNCHED]


def test_a_wildcard_typed_into_the_box_is_a_literal(flask_client, on_disk):
    """Unescaped, `%` would match every session — the search would silently
    stop filtering at the moment the operator typed a character."""
    _seed(_ON_DISK, title="100% done")
    _seed(_LAUNCHED, title="nothing to see")
    on_disk.update({_ON_DISK, _LAUNCHED})

    assert _ids(resumable.list_resumable("%")) == [_ON_DISK]


def test_limit_bounds_the_list(flask_client, on_disk):
    for i in range(5):
        sid = f"dddddddd-0000-0000-0000-00000000000{i}"
        _seed(sid, last_seen=f"2026-01-0{i + 1}T00:00:00")
        on_disk.add(sid)

    assert len(resumable.list_resumable(limit=3)) == 3


# ── the route ─────────────────────────────────────────────────────────

def test_route_serves_the_list(flask_client, on_disk):
    _seed(_ON_DISK, title="a session")
    on_disk.add(_ON_DISK)

    res = flask_client.get("/api/agent-runs/resumable")

    assert res.status_code == 200
    assert _ids(res.get_json()["sessions"]) == [_ON_DISK]


def test_route_passes_the_query_through(flask_client, on_disk):
    _seed(_ON_DISK, title="keep me")
    _seed(_LAUNCHED, title="drop me")
    on_disk.update({_ON_DISK, _LAUNCHED})

    res = flask_client.get("/api/agent-runs/resumable?q=keep")

    assert _ids(res.get_json()["sessions"]) == [_ON_DISK]


def test_a_malformed_limit_falls_back_instead_of_400ing(flask_client, on_disk):
    """The limit is a client detail, not a request the operator made — a
    garbled one should still return a usable list."""
    _seed(_ON_DISK)
    on_disk.add(_ON_DISK)

    res = flask_client.get("/api/agent-runs/resumable?limit=banana")

    assert res.status_code == 200
    assert _ids(res.get_json()["sessions"]) == [_ON_DISK]


def test_the_resumable_route_is_not_shadowed_by_the_single_run_route(
        flask_client, on_disk):
    """`/<trace_id>` would happily match the literal "resumable" and 404 on a
    run by that name."""
    res = flask_client.get("/api/agent-runs/resumable")

    assert res.status_code == 200
    assert "sessions" in res.get_json()


# ── the single-session question the trace page asks ───────────────────

@pytest.fixture
def tier_on(monkeypatch):
    """`is_resumable` gates on the tier, which the picker does not: the sheet
    reads `enabled` off launch-options beside the list, while the trace page's
    Resume button is the whole affordance and must not offer a launch the route
    would refuse. The suite ships the tier off, so these tests turn it on."""
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


def test_is_resumable_matches_what_the_picker_offers(flask_client, on_disk,
                                                     tier_on):
    """One rule, asked two ways. A page showing Resume where the sheet hides it
    would send the operator at a launch the route then refuses."""
    _seed(_ON_DISK)
    _seed("eeeeeeee-0000-0000-0000-00000000000e")
    on_disk.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is True
    assert resumable.is_resumable(
        "eeeeeeee-0000-0000-0000-00000000000e") is False


def test_is_resumable_refuses_a_run_still_live(flask_client, on_disk,
                                              tier_on):
    """Continuing a conversation that is still running would put two processes
    on one session id."""
    _seed(_LAUNCHED)
    on_disk.add(_LAUNCHED)
    store.upsert_run(_RUN_TRACE, status="running")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)
    registry.reserve_run(_RUN_TRACE)

    assert resumable.is_resumable(_LAUNCHED) is False


def test_is_resumable_answers_a_reaped_run_yes(flask_client, on_disk,
                                              tier_on):
    """The bug this exists for: the server was stopped, so the registry no
    longer claims the run — but the transcript is still on disk and the CLI can
    reopen it."""
    _seed(_LAUNCHED)
    on_disk.add(_LAUNCHED)
    store.upsert_run(_RUN_TRACE, status="running")
    store.set_cli_session(_RUN_TRACE, _LAUNCHED)
    store.reap_orphaned_runs()

    assert resumable.is_resumable(_LAUNCHED) is True


def test_the_synthetic_id_is_not_resumable(flask_client, on_disk, tier_on):
    on_disk.add(_RUN_TRACE)

    assert resumable.is_resumable(_RUN_TRACE) is False
    assert resumable.is_resumable("") is False


def test_is_resumable_is_false_while_the_tier_is_off(flask_client, on_disk):
    """Resume launches through `POST /api/agent-runs`, which a disabled tier
    refuses — offering the button there would be a control that cannot work."""
    _seed(_ON_DISK)
    on_disk.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is False


# ── sessions the CLI holds off-pane ───────────────────────────────────

def test_a_session_the_cli_still_hosts_is_not_offered(flask_client, provider,
                                                      tier_on):
    """The defect: a running conversation offered for resume.

    Claude Code hosts a backgrounded session on its own daemon's PTY, so it
    owns no tmux pane and the registry reads it exactly like a session that
    exited — while the CLI is still driving it. Nothing about the row says
    otherwise: `status` is active and `ended_at` is NULL, which is also true of
    every session that was ever killed."""
    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)
    provider.live.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is False
    assert resumable.list_resumable() == []


def test_the_same_session_is_offered_once_the_cli_stops_hosting_it(
        flask_client, provider, tier_on):
    """The exclusion tracks the CLI's answer, not the row: a session that has
    genuinely stopped keeps its Resume affordance, which is the whole reason
    the bar exists."""
    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is True


def test_a_cli_that_cannot_be_asked_leaves_the_verdict_alone(
        flask_client, provider, in_a_pane, tier_on):
    """None is "nobody could be asked", not "nothing is running".

    regin ingests traces from installs whose CLI it cannot run, so the probe
    failing must land the caller exactly where it stood before the probe
    existed — deciding on the pane registry alone."""
    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)
    provider.answers_live = False

    assert resumable.is_resumable(_ON_DISK) is True

    in_a_pane.add(_ON_DISK)
    assert resumable.is_resumable(_ON_DISK) is False


def test_a_session_the_cli_holds_is_refused_even_when_regin_calls_it_ended(
        flask_client, provider, tier_on):
    """The reap is not the last word on whether a process exists.

    An SDK child can outlive the `regin serve` restart that marked its run
    `exited`; the row then says ended while the CLI still holds the id, and
    resuming would start a second process against it. Observed live on
    52932e55, whose `agent_runs` row read `exited — server restarted` twelve
    hours after its pid stopped existing in regin's bookkeeping and not in the
    process table."""
    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)
    provider.live.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is False


def test_the_probe_is_skipped_for_a_candidate_already_settled(
        flask_client, provider, in_a_pane, tier_on):
    """It is the only check here that spawns a process, so a session the free
    checks already refused must not pay for it."""
    _seed(_ON_DISK)
    in_a_pane.add(_ON_DISK)

    assert resumable.is_resumable(_ON_DISK) is False
    assert provider.live_calls == 0


def test_only_a_held_session_is_reported_as_held(flask_client, provider,
                                                 in_a_pane, tier_on):
    """The page draws a third state off this, so the refusals must not blur:
    "nothing to resume" and "alive somewhere regin cannot type" look identical
    from the outside and read very differently to an operator."""
    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)
    provider.live.add(_ON_DISK)
    assert resumable.resume_refusal(_ON_DISK) == resumable.REFUSED_CLI_HELD

    # Same empty rail, different reason: no transcript is a dead conversation.
    _seed(_LAUNCHED)
    assert resumable.resume_refusal(_LAUNCHED) == 'no_transcript'

    # And a session with a pane of its own is steerable, not held.
    in_a_pane.add(_ON_DISK)
    assert resumable.resume_refusal(_ON_DISK) == 'in_pane'


def test_the_bridge_block_serves_the_held_flag(flask_client, provider,
                                               tier_on):
    """What the trace view actually reads."""
    from web.blueprints.trace.sessions import _bridge_reachability

    _seed(_ON_DISK)
    provider.transcripts.add(_ON_DISK)
    provider.live.add(_ON_DISK)

    state = _bridge_reachability(_ON_DISK)
    assert state['resumable'] is False
    assert state['cli_held'] is True


def test_the_live_set_is_read_once_for_a_whole_page(flask_client, provider):
    """The probe shells out to the CLI and the loop runs over the overfetched
    window, so a per-candidate call would put a subprocess per row on every
    keystroke in the search box."""
    for i in range(5):
        sid = f"cccccccc-0000-0000-0000-00000000000{i}"
        _seed(sid)
        provider.transcripts.add(sid)

    assert len(resumable.list_resumable()) == 5
    assert provider.live_calls == 1
