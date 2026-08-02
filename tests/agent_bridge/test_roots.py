"""Where a session's autocomplete is rooted (`lib/agent_bridge/roots.py`).

Two tiers of live session keep their cwd in two different tables, and both
composer surfaces (`/` commands, `@` files) resolve through here, so the
regression this pins is one bug in one function showing up as two dead menus:

  * a user-started terminal registers `bridge_panes` from the SessionStart
    hook, and that row still answers first,
  * a run regin launched through the Claude Agent SDK never registers one —
    `lib/agent_sdk/client.py` sets `REGIN_BRIDGE=0` for the child on purpose,
    so its pane would be regin's *server's* — and records its cwd in
    `agent_runs` instead. Asking only the pane registry left every spawned
    session rootless, which the fail-closed routes rendered as an empty `/`
    menu ("no command matches") rather than an error,
  * such a run is traced under two ids (`sdk-…` and its child `claude`
    session's) and the composer can address it by either, so both resolve,
  * and the fail-closed contract is unchanged: an unknown id, a run with no
    recorded cwd, and a cwd that no longer exists all stay rootless — regin's
    own tree is never stood in for a session that is not regin's.
"""

from __future__ import annotations

from pathlib import Path

from lib.agent_bridge import roots
from lib.agent_sdk import store as sdk_store
from lib.orm.engine import get_connection


def _seed_pane(trace_id: str, cwd: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, reachable, cwd) VALUES (?, '%0', 111, 222, 1, ?)",
            (trace_id, cwd))
        conn.commit()
    finally:
        conn.close()


def _seed_run(trace_id: str, cwd: str | None, *, child: str | None = None):
    sdk_store.upsert_run(trace_id, status="running", cwd=cwd)
    if child:
        sdk_store.set_cli_session(trace_id, child)


# ── the tmux tier is unchanged ───────────────────────────────


def test_pane_registry_answers_first(tmp_db, tmp_path):
    _seed_pane("t-pane", str(tmp_path))
    assert roots.session_cwd("t-pane") == tmp_path


def test_pane_row_wins_over_a_run_row(tmp_db, tmp_path):
    """Only a tmux session can be *re*-registered on resume, so if one id
    somehow exists in both tables the pane row is the fresher fact."""
    pane, run = tmp_path / "pane", tmp_path / "run"
    pane.mkdir()
    run.mkdir()
    _seed_pane("t-both", str(pane))
    _seed_run("t-both", str(run))
    assert roots.session_cwd("t-both") == pane


# ── the SDK tier, addressed by either of its ids ─────────────


def test_spawned_run_cwd_resolves(tmp_db, tmp_path):
    _seed_run("sdk-abc123", str(tmp_path))
    assert roots.session_cwd("sdk-abc123") == tmp_path
    assert roots.session_dir("sdk-abc123") == tmp_path


def test_spawned_run_resolves_by_child_session_id(tmp_db, tmp_path):
    """The session list's canonical id for a merged run is the child's, so
    that is the id the composer sends when the operator arrives from it."""
    _seed_run("sdk-abc123", str(tmp_path), child="child-session-id")
    assert roots.session_cwd("child-session-id") == tmp_path


# ── still fail-closed ────────────────────────────────────────


def test_unknown_id_stays_rootless(tmp_db):
    assert roots.session_cwd("no-such-session") is None
    assert roots.session_dir("no-such-session") is None


def test_run_without_a_recorded_cwd_stays_rootless(tmp_db):
    _seed_run("sdk-nocwd", None)
    assert roots.session_cwd("sdk-nocwd") is None


def test_vanished_run_cwd_is_not_a_directory(tmp_db, tmp_path):
    """A run row outlives the directory it names (a removed worktree)."""
    gone = tmp_path / "gone"
    _seed_run("sdk-gone", str(gone))
    assert roots.session_cwd("sdk-gone") == gone
    assert roots.session_dir("sdk-gone") is None


def test_blank_trace_id_queries_neither_tier(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("no lookup should happen for a blank id")

    monkeypatch.setattr(roots.store, "get_pane_cwd", _explode)
    monkeypatch.setattr(sdk_store, "find_run", _explode)
    assert roots.session_cwd("") is None


def test_project_root_is_independent_of_the_tier(tmp_path):
    """Provenance still walks up to the nearest `.claude/`, wherever the cwd
    came from — and has no fallback when there is none."""
    nested = tmp_path / "proj" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "proj" / ".claude").mkdir()
    assert roots.project_root(nested) == tmp_path / "proj"
    assert roots.project_root(Path(tmp_path / "elsewhere")) is None
