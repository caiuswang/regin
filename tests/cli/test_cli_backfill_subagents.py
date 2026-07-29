"""`regin trace backfill-subagents` — the repair path for ended sessions.

The live reconcile triggers (`SubagentStart`/`SubagentStop`, the `Stop` +
`SessionEnd` sweep, the rescan poll) all need a *live* session, so a session
ingested under an older reconciler can only be repaired from this command.
These tests pin its contract: who gets called, what is skipped, and that a
bare invocation writes nothing.

Both providers' modules are replaced with fakes registered in `sys.modules`,
so no transcript dirs or DB are touched — only the command's own control flow.
"""

from __future__ import annotations

import sys
import types

import pytest
from typer.testing import CliRunner

import cli.commands.trace as trace_cmd
from cli.app import app

runner = CliRunner()


class _FakeProviderModule(types.ModuleType):
    """Stands in for `lib.trace.<provider>_subagents`."""

    def __init__(self, name: str, discovered: list[str], result: dict):
        super().__init__(name)
        self._discovered = discovered
        self._result = result
        self.reconciled: list[str] = []

    def discover_subagent_sessions(self) -> list[str]:
        return list(self._discovered)

    def reconcile(self, trace_id: str) -> dict:
        self.reconciled.append(trace_id)
        return dict(self._result)


@pytest.fixture
def db_fakes(monkeypatch):
    """Both providers faked, but the REAL `_session_backfill_state` — so the
    liveness guard runs against the autouse `tmp_db`."""
    kimi = _FakeProviderModule(
        "fake_kimi_subagents", ["k1", "k2"],
        {"subagents": 1, "tool_spans": 3, "turns": 2, "launches_closed": 1})
    claude = _FakeProviderModule(
        "fake_claude_subagents", ["c1"],
        {"subagents": 1, "stamped": 1, "cost_usd": 0.5, "nested_parented": 0})
    for mod in (kimi, claude):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.setattr(trace_cmd, "_SUBAGENT_BACKFILL", {
        "kimi": ("fake_kimi_subagents", "reconcile",
                 ("tool_spans", "turns", "launches_closed")),
        "claude": ("fake_claude_subagents", "reconcile",
                   ("stamped", "nested_parented")),
    })
    return types.SimpleNamespace(kimi=kimi, claude=claude)


@pytest.fixture
def fakes(db_fakes, monkeypatch):
    """As `db_fakes`, plus: every discovered id has a row and none is live."""
    monkeypatch.setattr(trace_cmd, "_session_backfill_state",
                        lambda ids, cutoff: (set(ids), set()))
    return db_fakes


def _run(*argv: str):
    result = runner.invoke(app, ["trace", "backfill-subagents", *argv])
    assert "No such command" not in result.output, result.output
    return result


def test_command_is_registered():
    result = _run("--help")
    assert result.exit_code == 0
    assert "already-ended sessions" in result.output


def test_bare_invocation_refuses_to_write(fakes):
    result = _run()
    assert result.exit_code == 0
    assert "Refusing to rewrite spans without confirmation" in result.output
    assert fakes.kimi.reconciled == []
    assert fakes.claude.reconciled == []


def test_dry_run_lists_work_without_reconciling(fakes):
    result = _run("--dry-run")
    assert result.exit_code == 0
    assert "would reconcile k1" in result.output
    assert "would reconcile c1" in result.output
    assert fakes.kimi.reconciled == []
    assert fakes.claude.reconciled == []


def test_yes_reconciles_every_discovered_session(fakes):
    result = _run("--yes")
    assert result.exit_code == 0
    assert fakes.kimi.reconciled == ["k1", "k2"]
    assert fakes.claude.reconciled == ["c1"]
    assert "kimi: reconciled 2 session(s)" in result.output
    assert "tool_spans=6" in result.output


def test_write_run_labels_counters_as_restamped_not_deltas(fakes):
    # The reconcilers report no mutation count, so the summary must not imply
    # the numbers shrink on a repeat run.
    assert "(re)stamped, not deltas" in _run("--yes").output
    assert "(re)stamped, not deltas" not in _run("--dry-run").output


def test_write_prints_the_stale_server_caveat(fakes):
    assert "restart `regin serve`" in _run("--yes").output
    assert "restart `regin serve`" not in _run("--dry-run").output


def test_provider_flag_isolates_one_reconciler(fakes):
    _run("--yes", "--provider", "kimi")
    assert fakes.kimi.reconciled == ["k1", "k2"]
    assert fakes.claude.reconciled == []


def test_unknown_provider_is_rejected(fakes):
    result = _run("--yes", "--provider", "gemini")
    assert result.exit_code != 0
    assert fakes.kimi.reconciled == []


def test_session_flag_narrows_the_work_list(fakes):
    result = _run("--yes", "--session", "k2")
    assert fakes.kimi.reconciled == ["k2"]
    assert fakes.claude.reconciled == []
    assert "claude: 0 eligible session(s)" in result.output


def test_limit_caps_sessions_per_provider(fakes):
    _run("--yes", "--limit", "1")
    assert fakes.kimi.reconciled == ["k1"]
    assert fakes.claude.reconciled == ["c1"]


def test_sessions_absent_from_the_db_are_skipped(fakes, monkeypatch):
    monkeypatch.setattr(
        trace_cmd, "_session_backfill_state",
        lambda ids, cutoff: ({t for t in ids if t != "k2"}, set()))
    result = _run("--yes")
    assert fakes.kimi.reconciled == ["k1"]
    assert "skipped 1 with no `sessions` row" in result.output


def test_live_sessions_are_never_reconciled(fakes, monkeypatch):
    """A live session must not be reconciled: the reconcilers run in their
    non-`live` mode here and would mint synthetic `subagent.stop` markers,
    rendering an in-flight subagent as finished."""
    monkeypatch.setattr(trace_cmd, "_session_backfill_state",
                        lambda ids, cutoff: (set(ids), {"k1"}))
    result = _run("--yes")
    assert fakes.kimi.reconciled == ["k2"]
    assert "skipped 1 still live" in result.output


def test_both_skip_reasons_are_reported_together(fakes, monkeypatch):
    monkeypatch.setattr(
        trace_cmd, "_session_backfill_state",
        lambda ids, cutoff: ({t for t in ids if t != "k2"}, {"k1"}))
    result = _run("--yes", "--provider", "kimi")
    assert fakes.kimi.reconciled == []
    assert "skipped 1 with no `sessions` row and 1 still live" in result.output


def test_empty_work_list_is_a_clean_no_op(fakes, monkeypatch):
    monkeypatch.setattr(fakes.kimi, "_discovered", [])
    monkeypatch.setattr(fakes.claude, "_discovered", [])
    result = _run("--yes")
    assert result.exit_code == 0
    assert fakes.kimi.reconciled == []
    assert "kimi: 0 eligible session(s)" in result.output


def test_sessions_reporting_no_work_get_no_per_session_line(fakes, monkeypatch):
    monkeypatch.setattr(fakes.kimi, "_result", {
        "subagents": 2, "tool_spans": 0, "turns": 0, "launches_closed": 0})
    result = _run("--yes", "--provider", "kimi")
    assert fakes.kimi.reconciled == ["k1", "k2"]
    assert "reconciled 2 session(s)" in result.output
    assert "k1: {" not in result.output


def test_negative_limit_is_clamped_to_no_limit(fakes):
    # `trace_ids[:-1]` would silently drop the last session; -1 must mean
    # "no cap", matching the `--yes` path's counter semantics.
    assert _run("--dry-run", "--limit", "-1").output.count("would reconcile") == 3
    _run("--yes", "--limit", "-1")
    assert fakes.kimi.reconciled == ["k1", "k2"]


def test_a_failing_session_does_not_abort_the_sweep(fakes, monkeypatch):
    def _boom(trace_id):
        fakes.kimi.reconciled.append(trace_id)
        if trace_id == "k1":
            raise RuntimeError("transcript truncated mid-file")
        return {"subagents": 1, "tool_spans": 1, "turns": 1,
                "launches_closed": 0}

    monkeypatch.setattr(fakes.kimi, "reconcile", _boom)
    result = _run("--yes", "--provider", "kimi")
    # The second session still ran, the failure is reported, exit is non-zero.
    assert fakes.kimi.reconciled == ["k1", "k2"]
    assert "k1: FAILED — RuntimeError: transcript truncated mid-file" \
        in result.output
    assert "reconciled 1 session(s), 1 failed" in result.output
    assert result.exit_code == 1


def test_a_failing_provider_does_not_abort_the_other(fakes, monkeypatch):
    def _boom():
        raise OSError("permission denied: ~/.kimi/projects")

    monkeypatch.setattr(fakes.kimi, "discover_subagent_sessions", _boom)
    result = _run("--yes")
    assert fakes.claude.reconciled == ["c1"]
    assert "kimi: FAILED — OSError: permission denied" in result.output
    assert result.exit_code == 1


def test_empty_session_flag_is_rejected(fakes):
    result = _run("--yes", "--session", "  ")
    assert result.exit_code != 0
    assert fakes.kimi.reconciled == []


def test_idempotency_note_is_omitted_when_nothing_was_reconciled(fakes):
    result = _run("--yes", "--session", "nonexistent-id")
    assert result.exit_code == 0
    assert "(re)stamped, not deltas" not in result.output


def test_a_malformed_result_fails_without_leaking_phantom_counters(
        fakes, monkeypatch):
    """Half-summing a bad result must not credit the totals of a session the
    summary reports as failed."""
    monkeypatch.setattr(fakes.kimi, "_result",
                        {"tool_spans": 5, "turns": "not-a-number"})
    result = _run("--yes", "--provider", "kimi")
    assert "FAILED — TypeError" in result.output
    assert "tool_spans" not in result.output.split("FAILED")[-1]
    assert "reconciled 0 session(s), 2 failed" in result.output
    assert result.exit_code == 1


def test_totals_keep_keys_a_later_result_omits(fakes, monkeypatch):
    """`reconcile_claude_subagents` returns 3 keys on its early-return paths
    and 4 on the success path, so summing keyed off the latest result alone
    would silently drop a counter from the summary."""
    results = iter([
        {"subagents": 2, "stamped": 2, "cost_usd": 1.5, "nested_parented": 3},
        {"subagents": 0, "stamped": 0, "cost_usd": 0.0},
    ])
    monkeypatch.setattr(fakes.kimi, "reconcile", lambda t: next(results))
    result = _run("--yes", "--provider", "kimi")
    assert "nested_parented=3" in result.output


def test_default_idle_window_matches_the_serve_time_threshold():
    """The guard is only safe while the CLI and the renderer agree on who is
    running; drifting either constant re-opens the corruption class."""
    from lib.trace.pending_spans import INACTIVE_THRESHOLD_SEC
    assert trace_cmd._DEFAULT_IDLE_MINUTES * 60 == INACTIVE_THRESHOLD_SEC


def test_an_absurd_idle_window_reports_instead_of_crashing(fakes):
    result = _run("--dry-run", "--idle-minutes", "99999999999")
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_registry_points_at_real_importable_reconcilers():
    """The registry is the ONLY wiring between this CLI and the reconcilers;
    a typo there is invisible to every fake-backed test above."""
    import importlib
    assert set(trace_cmd._SUBAGENT_BACKFILL) == {"kimi", "claude"}
    for module_path, fn_name, report_keys in \
            trace_cmd._SUBAGENT_BACKFILL.values():
        module = importlib.import_module(module_path)
        assert callable(getattr(module, "discover_subagent_sessions"))
        assert callable(getattr(module, fn_name))
        assert report_keys


_OLD = "2020-01-01T00:00:00"      # unambiguously stale in any timezone


def _cutoff():
    return trace_cmd._idle_cutoff(10)


def _seed_sessions(rows: list[tuple[str, str | None, str]]) -> None:
    """Insert `(trace_id, status, last_seen)` rows into the autouse `tmp_db`."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen) "
            "VALUES (?, ?, ?, ?)",
            [(t, s, _OLD, seen) for t, s, seen in rows])
        conn.commit()
    finally:
        conn.close()


def test_backfill_state_reads_the_real_schema():
    """Runs against a real sqlite DB (the autouse `tmp_db`), so the query and
    the `sqlite3.Row` access are exercised, not mocked."""
    _seed_sessions([("a", "ended", _OLD), ("c", None, _OLD)])
    known, live = trace_cmd._session_backfill_state(["a", "b", "c"], _cutoff())
    assert known == {"a", "c"}
    assert live == set()


def test_backfill_state_chunks_past_the_sqlite_variable_cap(monkeypatch):
    # A single `IN (?,…)` wider than SQLITE_MAX_VARIABLE_NUMBER raises "too
    # many SQL variables" (999 on builds before 3.32). This build's cap is far
    # higher, so assert the CHUNKING ITSELF (query count), not just the result
    # — otherwise dropping the loop would leave the test green here.
    ids = [f"s{i}" for i in range(2500)]
    _seed_sessions([(t, "ended", _OLD) for t in ids[::2]])
    assert trace_cmd._session_backfill_state(ids, _cutoff())[0] == set(ids[::2])

    import lib.orm.engine as engine
    real_get_connection = engine.get_connection
    queries = []

    class _Counting:
        """`sqlite3.Connection.execute` is read-only, so proxy instead."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            queries.append(sql)
            return self._conn.execute(sql, *args)

        def close(self):
            self._conn.close()

    def _counting_connection():
        return _Counting(real_get_connection())

    # `monkeypatch.undo()` is off-limits here: the autouse `tmp_db` fixture
    # shares this same monkeypatch instance, so undoing would restore the real
    # DB_PATH mid-test.
    monkeypatch.setattr(engine, "get_connection", _counting_connection)
    monkeypatch.setattr(trace_cmd, "_SQL_VARS_PER_CHUNK", 7)
    known, _ = trace_cmd._session_backfill_state(ids[:25], _cutoff())
    assert known == set(ids[:25:2])
    assert len(queries) == 4          # ceil(25 / 7)


def test_backfill_state_short_circuits_on_empty_input():
    assert trace_cmd._session_backfill_state([], _cutoff()) == (set(), set())


# ── the liveness guard, driven end to end ─────────────────────
#
# `sessions.last_seen` is written in several shapes — naive local from the
# hook path, `...Z` / space-separated UTC from sqlite's `datetime('now')`.
# A live session in ANY of them must be skipped, or the reconciler mints
# synthetic `subagent.stop` markers over a running subagent.

def _stamp(delta_minutes: int, shape: str) -> str:
    from datetime import datetime, timedelta, timezone
    local = datetime.now() - timedelta(minutes=delta_minutes)
    utc = local.astimezone().astimezone(timezone.utc)
    return {
        "naive": local.isoformat(),
        "z": utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z",
        "offset": utc.isoformat(),
        "space": utc.strftime("%Y-%m-%d %H:%M:%S"),
    }[shape]


@pytest.mark.parametrize("shape", ["naive", "z", "offset", "space"])
def test_a_recently_active_session_is_live_in_every_stamp_shape(shape):
    _seed_sessions([("busy", "active", _stamp(0, shape))])
    _, live = trace_cmd._session_backfill_state(
        ["busy"], trace_cmd._idle_cutoff(10))
    assert live == {"busy"}, f"{shape} stamp misread as inactive"


@pytest.mark.parametrize("shape", ["naive", "z", "offset", "space"])
def test_a_long_quiet_session_is_reconcilable_in_every_stamp_shape(shape):
    # Three days, not hours: for a zone-less stamp the window is
    # `idle_minutes + the host's UTC offset`, which reaches 14h at UTC+14.
    _seed_sessions([("stale", "active", _stamp(60 * 24 * 3, shape))])
    _, live = trace_cmd._session_backfill_state(
        ["stale"], trace_cmd._idle_cutoff(10))
    assert live == set()


def test_an_unreadable_last_seen_counts_as_live():
    # The safe direction: never write synthetic stop markers on a row we
    # cannot date. Mirrors `merge._session_is_inactive` returning False.
    _seed_sessions([("bad", "active", "not-a-timestamp")])
    _, live = trace_cmd._session_backfill_state(
        ["bad"], trace_cmd._idle_cutoff(10))
    assert live == {"bad"}


def test_an_ended_session_is_never_live_however_recent():
    _seed_sessions([("done", "ended", _stamp(0, "naive"))])
    _, live = trace_cmd._session_backfill_state(
        ["done"], trace_cmd._idle_cutoff(10))
    assert live == set()


def test_an_ambiguous_naive_stamp_is_live_under_either_reading():
    """A naive stamp carries no zone, and sqlite's `datetime('now')` writes
    UTC while the hook path writes local. Whichever it is, a session that
    would be recent under EITHER reading must be treated as live."""
    from datetime import datetime
    offset = datetime.now().astimezone().utcoffset()
    if not offset or offset.total_seconds() <= 0:
        pytest.skip("host is at or west of UTC — the branch is a no-op there")
    # Stale as local, but only just-now if the stamp was really UTC.
    minutes = int(offset.total_seconds() // 60) - 5
    _seed_sessions([("ambiguous", "active", _stamp(minutes, "naive"))])
    _, live = trace_cmd._session_backfill_state(["ambiguous"], _cutoff())
    assert live == {"ambiguous"}


def test_idle_minutes_flag_changes_which_sessions_are_reconciled(db_fakes):
    """Drives `--idle-minutes` through the real guard: stubbing out either
    the flag or `_idle_cutoff` must make this fail. Margins are wide enough
    to clear the naive-stamp ambiguity window above."""
    quiet = 60 * 24 * 3      # three days
    _seed_sessions([("k1", "active", _stamp(quiet, "naive")),
                    ("k2", "ended", _stamp(quiet, "naive"))])
    result = _run("--yes", "--provider", "kimi")
    assert db_fakes.kimi.reconciled == ["k1", "k2"]

    # A window wider than the gap counts k1 as live again; k2 is `ended`, so
    # it stays reconcilable regardless of the window.
    db_fakes.kimi.reconciled.clear()
    result = _run("--yes", "--provider", "kimi",
                  "--idle-minutes", str(quiet * 2))
    assert db_fakes.kimi.reconciled == ["k2"]
    assert "skipped 1 still live" in result.output


def test_a_session_with_no_row_is_skipped_by_the_real_query(db_fakes):
    _seed_sessions([("k1", "ended", _stamp(30, "naive"))])
    result = _run("--yes", "--provider", "kimi")
    assert db_fakes.kimi.reconciled == ["k1"]
    assert "skipped 1 with no `sessions` row" in result.output
