"""What the launch sheet may offer to continue (`lib/agent_sdk/resumable.py`).

The picker's whole value is that everything in it can actually be resumed. A
trace row exists for every session regin ever saw, but `claude --resume` needs
the provider's transcript still on disk and the launch route refuses a run it is
still holding — so the list is defined by those exclusions, and these tests pin
each one. The rest pin what an operator needs *per row* to act on it: which id,
which cwd, and whether picking it continues the trace or opens a new one.
"""

from __future__ import annotations

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
def on_disk(monkeypatch):
    """Control which ids the provider claims to have a transcript for.

    Every test declares its own set: the real lookup globs the operator's
    `~/.claude/projects`, which would make the suite depend on the machine it
    runs on.
    """
    present: set[str] = set()

    class _Provider:
        def find_session_transcript(self, session_id):
            return f"/t/{session_id}.jsonl" if session_id in present else None

    monkeypatch.setattr("lib.providers.get_active_provider",
                        lambda *a, **k: _Provider())
    return present


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
