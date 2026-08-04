"""Tests for `regin stats` and the `lib.trace.session_stats` metric layer.

The metrics are gap-derived: a session's timeline is a list of point-in-time
spans, and the interval *preceding* each span is the work that produced it.
The fixtures below build timelines with deliberate gaps so each threshold
(active ≤60s, engaged ≤5m, and everything above) is exercised at its boundary.

The autouse `tmp_db` fixture (repo-root conftest) isolates the SQLite file.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

from typer.testing import CliRunner

import pytest

from cli import output
from cli.app import app
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.orm.models.trace import Session as SessionModel
from lib.trace.session_stats import (
    ACTIVE_GAP_MS, ENGAGED_GAP_MS, StatsFilter, _project_of, classify_command,
    engaged_windows, group_by, load_activity, load_commands, load_sessions,
    merge_intervals, split_by_day, summarize,
)

runner = CliRunner()

BASE = datetime(2026, 6, 1, 9, 0, 0)


@pytest.fixture
def cli_out(monkeypatch):
    """Capture `cli.output` writes.

    CliRunner swaps `sys.stdout`, but `cli.output` resolved its sink at
    import time, so `result.output` stays empty for commands that render
    through the output helpers. Redirect the sink itself instead.
    """
    buf = io.StringIO()
    monkeypatch.setattr(output, "_stdout", buf)
    return buf


def _ts(offset_s: float) -> str:
    return (BASE + timedelta(seconds=offset_s)).isoformat()


def _seed_session(trace_id: str, events, *, cwd="/Users/taowang/regin",
                  is_test=0, origin="session", started=None, cost=1.5):
    """`events` is a list of (offset_seconds, span_name, attributes|None)."""
    with SessionLocal() as s:
        s.add(SessionModel(
            trace_id=trace_id, title=f"title {trace_id}",
            started_at=started or _ts(0), last_seen=_ts(events[-1][0]),
            cwd=cwd, model="claude-opus-5", agent_type="claude", origin=origin,
            is_test=is_test, prompts=1, tool_calls=len(events),
            cost_usd=cost, input_tokens=10, output_tokens=20,
        ))
        for i, event in enumerate(events):
            offset, name, attrs = event[0], event[1], event[2]
            span_id = event[3] if len(event) > 3 else f"{trace_id}-{i}"
            agent_id = event[4] if len(event) > 4 else None
            status = "PENDING" if str(span_id).startswith(
                ("pending-", "permreq-", "promptlive-")) else "OK"
            s.add(SessionSpan(
                trace_id=trace_id, span_id=span_id, parent_id=None,
                name=name, kind="internal", start_time=_ts(offset),
                attributes=json.dumps(attrs or {}), status_code=status,
                agent_id=agent_id,
            ))
        s.commit()


def _simple_timeline():
    """30s + 30s active, then a 2-minute gap (engaged, not active)."""
    return [
        (0, "prompt", None),
        (30, "assistant.thinking", None),
        (60, "tool.Bash", {"command_preview": "pytest -q"}),
        (180, "tool.Edit", {"file_path": "/a.py"}),
    ]


# --- pure helpers -----------------------------------------------------------

def test_merge_intervals_unions_overlapping_windows():
    a = (datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 10))
    b = (datetime(2026, 6, 1, 9, 30), datetime(2026, 6, 1, 11))
    c = (datetime(2026, 6, 1, 12), datetime(2026, 6, 1, 13))
    assert merge_intervals([a, b, c]) == [
        (datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 11)),
        (datetime(2026, 6, 1, 12), datetime(2026, 6, 1, 13)),
    ]


def test_merge_intervals_handles_full_containment():
    outer = (datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 12))
    inner = (datetime(2026, 6, 1, 10), datetime(2026, 6, 1, 11))
    assert merge_intervals([outer, inner]) == [outer]


def test_split_by_day_splits_a_window_across_midnight():
    window = (datetime(2026, 6, 1, 23, 0), datetime(2026, 6, 2, 1, 0))
    assert split_by_day([window]) == {
        "2026-06-01": 3_600_000,
        "2026-06-02": 3_600_000,
    }


def test_project_of_decodes_harness_scratchpad_slug():
    cwd = "/private/tmp/claude-501/-Users-taowang-regin/abc-uuid/scratchpad/x"
    assert _project_of(cwd) == "regin"


def test_project_of_uses_top_level_dir_under_home():
    assert _project_of("/Users/taowang/regin/frontend") == "regin"
    assert _project_of(None) == "(unknown)"


def test_classify_command_prefers_intent_over_interpreter():
    assert classify_command(".venv/bin/python -m pytest -q") == "tests"
    assert classify_command("npx vite build") == "build"
    assert classify_command("git status") == "git"
    assert classify_command("rg foo lib/") == "search/inspect"
    assert classify_command("./weird-binary") == "other shell"


def test_classify_command_ignores_repo_path_as_regin_invocation():
    # A bare \bregin\b would match the repo path in nearly every command.
    assert classify_command("ls /Users/taowang/regin/lib") == "search/inspect"
    assert classify_command(".venv/bin/python cli/regin.py doctor") == "regin cli"


# --- gap thresholds ---------------------------------------------------------

def test_active_and_engaged_thresholds_partition_the_timeline():
    _seed_session("t1", _simple_timeline())
    rows = load_sessions(StatsFilter(min_engaged_ms=0))
    assert len(rows) == 1
    row = rows[0]
    # 30s + 30s under the active threshold; the 120s gap is engaged only.
    assert row.active_ms == 60_000
    assert row.engaged_ms == 180_000
    assert row.wall_ms == 180_000
    # The 120s gap ends at tool.Edit — a slow agent, not a human. Human time
    # is event-derived, so it is NOT engaged - active.
    assert row.human_ms == 0
    assert row.agent_ms == 180_000


def test_gap_above_engaged_threshold_is_excluded_from_engaged():
    _seed_session("t2", [
        (0, "prompt", None),
        (30, "tool.Bash", {"command_preview": "ls"}),
        (30 + ENGAGED_GAP_MS / 1000 + 60, "tool.Bash", {"command_preview": "ls"}),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.active_ms == 30_000
    assert row.engaged_ms == 30_000
    assert row.wall_ms > ENGAGED_GAP_MS


def test_gap_exactly_at_active_threshold_counts_as_active():
    _seed_session("t3", [
        (0, "prompt", None),
        (ACTIVE_GAP_MS / 1000, "assistant.thinking", None),
    ])
    assert load_sessions(StatsFilter(min_engaged_ms=0))[0].active_ms == ACTIVE_GAP_MS


# --- session selection ------------------------------------------------------

def test_test_sessions_are_excluded_by_default():
    _seed_session("real", _simple_timeline())
    _seed_session("fake", _simple_timeline(), is_test=1)
    assert {r.trace_id for r in load_sessions(StatsFilter(min_engaged_ms=0))} == {"real"}
    both = load_sessions(StatsFilter(min_engaged_ms=0, include_test=True))
    assert {r.trace_id for r in both} == {"real", "fake"}


def test_non_session_origins_are_excluded_by_default():
    _seed_session("real", _simple_timeline())
    _seed_session("wf", _simple_timeline(), origin="workflow")
    assert {r.trace_id for r in load_sessions(StatsFilter(min_engaged_ms=0))} == {"real"}
    flt = StatsFilter(min_engaged_ms=0, origins=("session", "workflow"))
    assert len(load_sessions(flt)) == 2


def test_min_engaged_filter_drops_trivial_sessions():
    _seed_session("tiny", [(0, "prompt", None), (5, "tool.Bash", {})])
    assert load_sessions(StatsFilter(min_engaged_ms=60_000)) == []
    assert len(load_sessions(StatsFilter(min_engaged_ms=0))) == 1


# --- rework / churn ---------------------------------------------------------

def test_rework_counts_repeat_edits_of_the_same_file():
    _seed_session("churn", [
        (0, "prompt", None),
        (10, "tool.Edit", {"file_path": "/a.py"}),
        (20, "tool.Edit", {"file_path": "/a.py"}),
        (30, "tool.Edit", {"file_path": "/a.py"}),
        (40, "tool.Write", {"file_path": "/b.py"}),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.edits == 4
    assert row.files_touched == 2
    # /a.py touched 3x contributes 2 re-edits; /b.py contributes 0.
    assert row.rework_edits == 2


# --- attribution ------------------------------------------------------------

def test_activity_attributes_each_gap_to_the_span_that_ends_it():
    _seed_session("attr", _simple_timeline())
    by_name = {r["activity"]: r["ms"] for r in load_activity(StatsFilter(min_engaged_ms=0))}
    # 0→30 lands on assistant.thinking, 30→60 on the Bash call.
    assert by_name["thinking"] == 30_000
    assert by_name["shell"] == 30_000
    # The 120s gap ending at tool.Edit exceeds the active threshold.
    assert "editing" not in by_name


def test_container_spans_are_not_charged_activity_time():
    _seed_session("cont", [
        (0, "tool.Bash", {"command_preview": "ls"}),
        (10, "prompt", None),
        (20, "assistant.thinking", None),
    ])
    labels = {r["activity"] for r in load_activity(StatsFilter(min_engaged_ms=0))}
    assert labels == {"thinking"}


def test_shell_time_is_grouped_by_command_intent():
    _seed_session("sh", [
        (0, "prompt", None),
        (10, "tool.Bash", {"command_preview": "pytest -q"}),
        (30, "tool.Bash", {"command_preview": "git diff"}),
    ])
    rows = {r["command"]: r["ms"] for r in load_commands(StatsFilter(min_engaged_ms=0))}
    assert rows == {"tests": 10_000, "git": 20_000}


# --- calendar time ----------------------------------------------------------

def test_concurrent_sessions_are_counted_once_in_calendar_time():
    # Two sessions covering the same wall-clock minute.
    _seed_session("p1", [(0, "prompt", None), (30, "assistant.thinking", None)])
    _seed_session("p2", [(0, "prompt", None), (30, "assistant.thinking", None)])
    flt = StatsFilter(min_engaged_ms=0)
    totals = summarize(load_sessions(flt), engaged_windows(flt))
    assert totals.engaged_ms == 60_000       # summed per session
    assert totals.calendar_ms == 30_000      # deduped on the shared timeline
    assert totals.concurrency == 2.0


def test_engaged_windows_split_on_long_idle_gaps():
    _seed_session("w", [
        (0, "prompt", None),
        (30, "assistant.thinking", None),
        (30 + ENGAGED_GAP_MS / 1000 + 60, "prompt", None),
        (60 + ENGAGED_GAP_MS / 1000 + 60, "assistant.thinking", None),
    ])
    windows = engaged_windows(StatsFilter(min_engaged_ms=0))
    assert len(windows) == 2
    assert all((b - a).total_seconds() == 30 for a, b in windows)


def test_group_by_project_aggregates_sessions():
    _seed_session("g1", _simple_timeline(), cwd="/Users/taowang/regin")
    _seed_session("g2", _simple_timeline(), cwd="/Users/taowang/other")
    _seed_session("g3", _simple_timeline(), cwd="/Users/taowang/regin/frontend")
    rows = group_by(load_sessions(StatsFilter(min_engaged_ms=0)), "project")
    by_project = {r["project"]: r["sessions"] for r in rows}
    assert by_project == {"regin": 2, "other": 1}


# --- CLI --------------------------------------------------------------------

def test_stats_time_renders_the_headline_report(cli_out):
    _seed_session("cli1", _simple_timeline())
    result = runner.invoke(app, ["stats", "time", "--min-minutes", "0"])
    assert result.exit_code == 0, result.output
    rendered = cli_out.getvalue()
    assert "Calendar time (deduped)" in rendered
    assert "Agent time by activity" in rendered
    assert "Shell time by command intent" in rendered


def test_stats_time_json_is_machine_readable(cli_out):
    _seed_session("cli2", _simple_timeline())
    result = runner.invoke(
        app, ["stats", "time", "--min-minutes", "0", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(cli_out.getvalue())
    assert payload["totals"]["active_ms"] == 60_000
    assert payload["totals"]["sessions"] == 1
    assert any(r["activity"] == "thinking" for r in payload["activity"])


def test_stats_sessions_csv_exports_one_row_per_session(cli_out):
    _seed_session("cli3", _simple_timeline())
    _seed_session("cli4", _simple_timeline())
    result = runner.invoke(
        app, ["stats", "sessions", "--min-minutes", "0", "--format", "csv"])
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(io.StringIO(cli_out.getvalue())))
    assert len(rows) == 2
    assert {r["trace_id"] for r in rows} == {"cli3", "cli4"}
    assert rows[0]["engaged_ms"] == "180000"


def test_stats_out_flag_writes_a_file(tmp_path):
    _seed_session("cli5", _simple_timeline())
    dest = tmp_path / "export.csv"
    result = runner.invoke(app, [
        "stats", "sessions", "--min-minutes", "0",
        "--format", "csv", "--out", str(dest)])
    assert result.exit_code == 0, result.output
    assert "cli5" in dest.read_text()


def test_stats_by_rejects_an_unknown_dimension():
    _seed_session("cli6", _simple_timeline())
    result = runner.invoke(app, ["stats", "by", "nonsense"])
    assert result.exit_code == 2


def test_stats_time_exits_nonzero_when_nothing_matches():
    result = runner.invoke(app, ["stats", "time", "--since", "2099-01-01"])
    assert result.exit_code == 1


def test_stats_daily_reports_calendar_hours_per_day(cli_out):
    _seed_session("cli7", _simple_timeline())
    result = runner.invoke(
        app, ["stats", "daily", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(cli_out.getvalue())
    assert rows[0]["day"] == "2026-06-01"
    assert rows[0]["sessions"] == 1


def test_stats_rejects_an_unknown_format():
    _seed_session("cli8", _simple_timeline())
    result = runner.invoke(
        app, ["stats", "time", "--min-minutes", "0", "--format", "tsv"])
    assert result.exit_code == 2


# --- single-session postmortem ---------------------------------------------

def _postmortem_timeline():
    """Two requests: a short one, then a long one that thrashes one file."""
    return [
        (0, "prompt", {"text": "first ask"}),
        (20, "assistant.thinking", None),
        (40, "tool.Edit", {"file_path": "/a.py"}),
        (600, "prompt", {"text": "second ask"}),          # long idle before
        (630, "assistant.thinking", None),
        (660, "tool.Bash", {"command_preview": "pytest -q"}),
        (700, "tool.failure", {"tool_name": "Bash", "error": "Exit code 1\nboom"}),
        (740, "tool.Edit", {"file_path": "/b.py"}),
        (780, "tool.Edit", {"file_path": "/b.py"}),
        (900, "tool.Edit", {"file_path": "/b.py"}),        # 120s gap: engaged only
    ]


def test_session_detail_segments_on_each_prompt():
    from lib.trace.session_stats import session_detail
    _seed_session("pm", _postmortem_timeline())
    d = session_detail("pm")
    assert [s.prompt for s in d.segments] == ["first ask", "second ask"]
    # Request 0: 20s + 20s. The 560s gap before the next prompt exceeds the
    # engaged threshold, so it is not charged to either request.
    assert d.segments[0].engaged_ms == 40_000
    assert d.segments[0].edits == 1
    assert d.segments[1].edits == 3
    assert d.segments[1].failures == 1


def test_session_detail_activity_shares_use_the_active_threshold():
    from lib.trace.session_stats import session_detail
    _seed_session("pm2", _postmortem_timeline())
    d = session_detail("pm2")
    # Every charged gap must be <= the active threshold, so the activity
    # total can never exceed the session's active_ms.
    assert sum(r["ms"] for r in d.activity) <= d.stat.active_ms
    labels = {r["activity"] for r in d.activity}
    assert {"thinking", "editing", "shell"} <= labels


def test_session_detail_ranks_the_most_edited_file_first():
    from lib.trace.session_stats import session_detail
    _seed_session("pm3", _postmortem_timeline())
    d = session_detail("pm3")
    assert d.files[0] == {"file": "/b.py", "edits": 3}


def test_session_detail_reports_failures_with_a_one_line_detail():
    from lib.trace.session_stats import session_detail
    _seed_session("pm4", _postmortem_timeline())
    d = session_detail("pm4")
    assert len(d.failures) == 1
    assert d.failures[0]["tool"] == "Bash"
    assert d.failures[0]["detail"] == "Exit code 1"


def test_session_detail_slowest_steps_are_ordered_and_labelled():
    from lib.trace.session_stats import session_detail
    _seed_session("pm5", _postmortem_timeline())
    d = session_detail("pm5")
    assert d.slowest[0]["ms"] == 120_000
    assert "/b.py" in d.slowest[0]["label"]
    assert [g["ms"] for g in d.slowest] == sorted(
        (g["ms"] for g in d.slowest), reverse=True)


def test_session_detail_includes_test_and_non_session_origins():
    # A postmortem is always explicitly requested by trace_id, so the
    # "real session" filter must not hide it.
    from lib.trace.session_stats import session_detail
    _seed_session("wf1", _postmortem_timeline(), origin="workflow")
    _seed_session("tst1", _postmortem_timeline(), is_test=1)
    assert session_detail("wf1") is not None
    assert session_detail("tst1") is not None


def test_session_detail_returns_none_for_an_unknown_trace():
    from lib.trace.session_stats import session_detail
    assert session_detail("nope") is None


def test_resolve_trace_id_prefers_an_exact_match_over_a_prefix():
    from lib.trace.session_stats import resolve_trace_id
    _seed_session("abc", _simple_timeline())
    _seed_session("abcdef", _simple_timeline())
    assert resolve_trace_id("abc") == ["abc"]
    assert resolve_trace_id("abcd") == ["abcdef"]
    assert resolve_trace_id("zzz") == []


def test_stats_session_leads_with_a_verdict_not_a_breakdown(cli_out):
    _seed_session("pm6", _postmortem_timeline())
    result = runner.invoke(app, ["stats", "session", "pm6"])
    assert result.exit_code == 0, result.output
    rendered = cli_out.getvalue()
    assert "VERDICT:" in rendered
    # Composition tables are opt-in; the default view is the diagnosis.
    assert "Time by request" not in rendered


def test_stats_session_breakdown_flag_adds_the_tables(cli_out):
    _seed_session("pm6b", _postmortem_timeline())
    result = runner.invoke(app, ["stats", "session", "pm6b", "--breakdown"])
    assert result.exit_code == 0, result.output
    rendered = cli_out.getvalue()
    assert "VERDICT:" in rendered
    assert "Time by request" in rendered
    assert "Most-edited files" in rendered


def test_stats_session_json_exposes_every_view(cli_out):
    _seed_session("pm7", _postmortem_timeline())
    result = runner.invoke(app, ["stats", "session", "pm7", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(cli_out.getvalue())
    assert payload["session"]["trace_id"] == "pm7"
    assert payload["verdict"]
    assert "findings" in payload
    assert "segments" not in payload          # breakdown is opt-in


def test_stats_session_json_with_breakdown_exposes_every_view(cli_out):
    _seed_session("pm7b", _postmortem_timeline())
    result = runner.invoke(
        app, ["stats", "session", "pm7b", "--breakdown", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(cli_out.getvalue())
    assert [s["prompt"] for s in payload["segments"]] == ["first ask", "second ask"]
    assert payload["files"][0]["file"] == "/b.py"
    assert payload["failures"][0]["tool"] == "Bash"
    assert payload["slowest"][0]["ms"] == 120_000


def test_stats_session_errors_on_unknown_trace():
    result = runner.invoke(app, ["stats", "session", "missing"])
    assert result.exit_code == 1


def test_stats_session_errors_on_an_ambiguous_prefix():
    _seed_session("dup1", _simple_timeline())
    _seed_session("dup2", _simple_timeline())
    result = runner.invoke(app, ["stats", "session", "dup"])
    assert result.exit_code == 2


# --- append-only placeholder rows (lib/trace/merge.py retires these) --------

def test_pending_tool_placeholders_are_not_double_counted():
    """A blocking tool writes `pending-<tu>` at PreToolUse and a resolved row
    at PostToolUse. Counting both inflates tool counts and splits one gap."""
    from lib.trace.session_stats import load_commands
    _seed_session("pend", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Bash", {"command_preview": "pytest -q"}, "pending-abc"),
        (30, "tool.Bash", {"command_preview": "pytest -q"}, "abc"),
    ])
    rows = load_commands(StatsFilter(min_engaged_ms=0))
    assert rows == [{"command": "tests", "calls": 1, "ms": 30_000}]


def test_promptlive_placeholders_do_not_create_phantom_requests():
    from lib.trace.session_stats import session_detail
    _seed_session("plive", [
        (0, "promptlive", {"text": "real ask"}, "promptlive-deadbeef"),
        (1, "prompt", {"text": "real ask"}),
        (20, "assistant.thinking", None),
    ])
    d = session_detail("plive")
    assert [seg.prompt for seg in d.segments] == ["real ask"]


def test_pending_edit_rows_do_not_inflate_the_edit_count():
    _seed_session("pedit", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Edit", {"file_path": "/a.py"}, "pending-e1"),
        (20, "tool.Edit", {"file_path": "/a.py"}, "e1"),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.edits == 1
    assert row.rework_edits == 0


def test_pending_failure_rows_do_not_inflate_the_failure_count():
    _seed_session("pfail", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.failure", {"tool_name": "Bash"}, "permreq-f1"),
        (20, "tool.failure", {"tool_name": "Bash"}, "f1"),
    ])
    assert load_sessions(StatsFilter(min_engaged_ms=0))[0].failures == 1


def test_resolve_trace_id_treats_underscore_as_a_literal():
    """95 real trace ids contain `_`, a LIKE wildcard. Without ESCAPE a
    prefix silently matches unrelated sessions and reports them ambiguous."""
    from lib.trace.session_stats import resolve_trace_id
    _seed_session("session_aaa", _simple_timeline())
    _seed_session("sessionXbbb", _simple_timeline())
    assert resolve_trace_id("session_") == ["session_aaa"]
    assert resolve_trace_id("%") == []


def test_stats_rejects_an_empty_origin_list():
    _seed_session("orig", _simple_timeline())
    result = runner.invoke(app, ["stats", "time", "--origin", ",,,"])
    assert result.exit_code == 2


def test_stats_reports_an_unwritable_out_path(cli_out):
    _seed_session("outp", _simple_timeline())
    result = runner.invoke(app, [
        "stats", "sessions", "--min-minutes", "0",
        "--format", "csv", "--out", "/nonexistent-dir/x.csv"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, FileNotFoundError)


# --- diagnosis -------------------------------------------------------------

def _diag(trace_id):
    from lib.trace.session_stats import diagnose
    return {f.kind: f for f in diagnose(trace_id)}


def test_rework_charges_the_cycle_that_produced_each_correction():
    """A correction's cost is the thinking/testing since the previous edit,
    not the edit span itself."""
    _seed_session("rw", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Edit", {"file_path": "/a.py"}),      # first touch, free
        (20, "assistant.thinking", None),
        (50, "tool.Edit", {"file_path": "/b.py"}),      # first touch, free
        (80, "assistant.thinking", None),
        (110, "tool.Edit", {"file_path": "/a.py"}),     # correction: 60s cycle
    ])
    f = _diag("rw")["rework-loop"]
    assert f.minutes == 1.0
    assert "1 of 3 edits" in f.headline


def test_rework_is_silent_when_every_file_is_touched_once():
    _seed_session("norw", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Edit", {"file_path": "/a.py"}),
        (70, "tool.Edit", {"file_path": "/b.py"}),
        (130, "tool.Edit", {"file_path": "/c.py"}),
    ])
    assert "rework-loop" not in _diag("norw")


def test_failure_streak_resets_on_a_successful_edit():
    """Two failures, a working edit, then two more is not a 4x streak."""
    events = [(0, "prompt", {"text": "go"})]
    for i, name in enumerate(["tool.failure", "tool.failure", "tool.Edit",
                              "tool.failure", "tool.failure"]):
        attrs = {"tool_name": "Bash"} if name == "tool.failure" else {"file_path": "/a.py"}
        events.append((30 * (i + 1), name, attrs))
    _seed_session("fs", events)
    assert "failure-retry" not in _diag("fs")


def test_failure_streak_is_counted_once_not_once_per_event():
    """A 5-long streak is one finding; summing per-event inflated the cost
    past the session's own length."""
    events = [(0, "prompt", {"text": "go"})]
    events += [(30 * (i + 1), "tool.failure", {"tool_name": "Bash"})
               for i in range(5)]
    _seed_session("fs2", events)
    f = _diag("fs2")["failure-retry"]
    assert "1 failure streak" in f.headline
    # Window from the 1st to the 5th failure = 4 gaps of 30s.
    assert f.minutes == 2.0


def test_blocked_on_human_measures_the_wait_after_the_prompt():
    _seed_session("blk", [
        (0, "prompt", {"text": "go"}),
        (10, "permission.request", {}),
        (100, "tool.Bash", {"command_preview": "ls"}),   # 90s deciding
        (110, "tool.AskUserQuestion", {}),
        (170, "assistant.thinking", None),               # 60s deciding
    ])
    f = _diag("blk")["blocked-on-human"]
    assert f.minutes == 2.5
    assert "2x" in f.headline


def test_rereads_charge_only_the_reads_after_the_first():
    _seed_session("rr", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.Read", {"file_path": "/a.py"}),   # first read, free
        (70, "tool.Read", {"file_path": "/a.py"}),   # +40s
        (110, "tool.Read", {"file_path": "/a.py"}),  # +40s
    ])
    f = _diag("rr")["re-read"]
    assert f.minutes == pytest.approx(80 / 60, abs=0.01)
    assert "2 redundant re-reads" in f.headline


def test_diagnosis_verdict_names_convergence_failure():
    from lib.trace.session_stats import diagnose_session
    events = [(0, "prompt", {"text": "go"}), (10, "tool.Edit", {"file_path": "/a.py"})]
    events += [(30 * (i + 2), "tool.Edit", {"file_path": "/a.py"}) for i in range(9)]
    _seed_session("verd", events)
    d = diagnose_session("verd")
    assert "Convergence failure" in d.verdict
    assert d.attributed_ms > 0


def test_diagnosis_verdict_is_honest_when_nothing_is_wrong():
    from lib.trace.session_stats import diagnose_session
    _seed_session("clean", [
        (0, "prompt", {"text": "go"}),
        (20, "assistant.thinking", None),
        (40, "tool.Edit", {"file_path": "/a.py"}),
    ])
    d = diagnose_session("clean")
    # Nothing wasteful fired; all the time lands in the forward-work remainder.
    assert [f for f in d.findings if f.minutes] == []
    assert "mostly forward work" in d.verdict
    assert d.remainder_ms == d.stat.engaged_ms


def test_waste_plus_forward_work_exactly_equals_attended_time():
    """Every gap is awarded to exactly one line — no double counting (the
    per-event summing bug once reported 890 min on a 432 min session) and
    nothing silently unexplained."""
    from lib.trace.session_stats import diagnose_session
    _seed_session("bound", _postmortem_timeline())
    d = diagnose_session("bound")
    assert d.attributed_ms + d.remainder_ms == d.stat.engaged_ms


def test_your_typing_is_forward_work_not_agent_waste():
    """A prompt gap inside a rework interval must not be billed as rework."""
    from lib.trace.session_stats import diagnose_session
    _seed_session("mine", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Edit", {"file_path": "/a.py"}),
        (130, "prompt", {"text": "no, try again"}),   # 120s you typing
        (160, "tool.Edit", {"file_path": "/a.py"}),   # correction
    ])
    d = diagnose_session("mine")
    groups = {r["group"]: r["ms"] for r in d.remainder}
    assert groups["you typing / deciding"] == 120_000
    rework = next(f for f in d.findings if f.kind == "rework-loop")
    assert rework.minutes == 0.5      # only the 30s agent gap, not your 120s


def test_remainder_groups_harness_time_separately():
    from lib.trace.session_stats import diagnose_session
    _seed_session("harn", [
        (0, "prompt", {"text": "go"}),
        (30, "harness.task_reminder", {}),
        (60, "assistant.thinking", None),
        (90, "tool.Bash", {"command_preview": "ls"}),
    ])
    d = diagnose_session("harn")
    groups = {r["group"]: r["ms"] for r in d.remainder}
    assert groups["harness overhead"] == 30_000
    assert groups["model thinking"] == 30_000
    assert groups["one-off shell"] == 30_000


def test_a_gap_is_never_claimed_by_two_lenses():
    """A repeated test command inside a rework interval belongs to the
    narrower lens only, so the totals stay a partition."""
    from lib.trace.session_stats import analyse, build_timeline, _fetch_timeline
    _seed_session("dedup", _postmortem_timeline())
    tl, findings, remainder = analyse("dedup")
    claimed = sum(f.minutes for f in findings) * 60_000
    assert round(claimed) + sum(r["ms"] for r in remainder) == sum(tl.gaps)


def test_diagnosis_returns_none_for_an_unknown_trace():
    from lib.trace.session_stats import diagnose_session
    assert diagnose_session("nope") is None


# --- human vs agent time ---------------------------------------------------

def test_human_time_is_the_gap_ending_at_a_user_prompt():
    """A duration cutoff cannot tell a human from a slow agent — only the
    identity of the event that ends the gap can."""
    _seed_session("hum", [
        (0, "prompt", {"text": "first"}),
        (30, "assistant.thinking", None),        # 30s agent
        (150, "prompt", {"text": "second"}),     # 120s the user was typing
        (180, "assistant.thinking", None),       # 30s agent
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.engaged_ms == 180_000
    assert row.human_ms == 120_000
    assert row.agent_ms == 60_000


def test_slow_agent_work_is_not_counted_as_human_time():
    """43 h of >60s gaps across the corpus end at assistant.thinking; the old
    `engaged - active` definition called all of it human."""
    _seed_session("slowagent", [
        (0, "prompt", {"text": "go"}),
        (200, "assistant.thinking", None),   # 200s of model thinking
        (400, "tool.Bash", {"command_preview": "pytest -q"}),   # 200s test run
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.human_ms == 0
    assert row.agent_ms == 400_000
    # The old definition would have called all 400s human.
    assert row.engaged_ms - row.active_ms == 400_000


def test_wait_after_an_approval_request_is_human_time():
    _seed_session("appr", [
        (0, "prompt", {"text": "go"}),
        (10, "permission.request", {}),
        (100, "tool.Bash", {"command_preview": "ls"}),   # 90s you deciding
        (130, "assistant.thinking", None),               # 30s agent
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.human_ms == 90_000
    assert row.agent_ms == 40_000


def test_subagent_launch_prompts_are_not_human_time():
    """`prompt-sa-*` is the agent prompting itself, not you typing."""
    _seed_session("sa", [
        (0, "prompt", {"text": "go"}),
        (120, "prompt", {"agent_id": "a1", "text": "subtask"}, "prompt-sa-a1"),
        (240, "prompt", {"text": "real follow-up"}),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.human_ms == 120_000     # only the real follow-up counts
    assert row.agent_ms == 120_000


def test_human_and_agent_time_partition_attended_time():
    _seed_session("part", _postmortem_timeline())
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.human_ms + row.agent_ms == row.engaged_ms


# --- portability across agent harnesses ------------------------------------

def test_codex_apply_patch_spans_count_as_edits():
    """Codex records edits as tool.apply_patch; missing it reported edits=0
    and then produced a confident, wrong verdict."""
    _seed_session("cdx", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.apply_patch", {"tool_name": "apply_patch"}),
        (60, "tool.apply_patch", {"tool_name": "apply_patch"}),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.edits == 2
    assert row.files_touched == 0      # the span carries no file_path


def test_diagnosis_declares_a_blind_spot_instead_of_guessing():
    from lib.trace.session_stats import diagnose_session
    _seed_session("cdx2", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.apply_patch", {"tool_name": "apply_patch"}),
        (60, "tool.apply_patch", {"tool_name": "apply_patch"}),
    ])
    d = diagnose_session("cdx2")
    assert d.blind_spots
    assert "file path" in d.blind_spots[0]
    assert "2 edits cannot be attributed" in d.blind_spots[0]


def test_path_bearing_edits_still_feed_the_rework_lens():
    _seed_session("mixed", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.Edit", {"file_path": "/a.py"}),
        (90, "tool.Edit", {"file_path": "/a.py"}),
    ])
    row = load_sessions(StatsFilter(min_engaged_ms=0))[0]
    assert row.edits == 2 and row.files_touched == 1 and row.rework_edits == 1


# --- subagent lanes --------------------------------------------------------

def test_subagent_work_is_grouped_apart_from_the_main_thread():
    """Subagent spans carry the full work vocabulary (59k Bash, 18k Read
    corpus-wide). Without lane awareness they were billed to the main thread."""
    from lib.trace.session_stats import diagnose_session
    _seed_session("lane", [
        (0, "prompt", {"text": "go"}),
        (30, "assistant.thinking", None),                          # main
        (60, "tool.Bash", {"command_preview": "ls"}, "s1", "a1"),   # subagent
        (90, "tool.Read", {"file_path": "/x.py"}, "s2", "a1"),      # subagent
    ])
    d = diagnose_session("lane")
    groups = {r["group"]: r["ms"] for r in d.remainder}
    assert groups["subagent work"] == 60_000
    assert groups["model thinking"] == 30_000
    assert "one-off shell" not in groups


def test_delegation_reports_agent_hours_and_parallelism():
    """Two subagents working the same wall-clock minute is 2 agent-minutes in
    1 wall minute — invisible on the merged timeline."""
    from lib.trace.session_stats import build_timeline, delegation, _fetch_timeline
    _seed_session("par", [
        (0, "prompt", {"text": "go"}),
        (10, "tool.Bash", {"command_preview": "a"}, "p1", "a1"),
        (20, "tool.Bash", {"command_preview": "b"}, "p2", "a2"),
        (70, "tool.Bash", {"command_preview": "c"}, "p3", "a1"),
        (80, "tool.Bash", {"command_preview": "d"}, "p4", "a2"),
    ])
    g = delegation(build_timeline(_fetch_timeline("par")))
    assert g.agents == 2
    assert g.subagent_ms == 120_000          # 60s in each of two lanes
    assert g.wall_ms == 80_000               # but only 80s of wall clock
    assert g.parallelism > 1.0


def test_two_subagents_editing_one_file_is_not_rework():
    """Parallel work on a shared file is not one agent churning."""
    from lib.trace.session_stats import diagnose_session
    _seed_session("xagent", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.Edit", {"file_path": "/a.py"}, "e1", "a1"),
        (60, "tool.Edit", {"file_path": "/a.py"}, "e2", "a2"),
    ])
    d = diagnose_session("xagent")
    assert not [f for f in d.findings if f.kind == "rework-loop"]


def test_one_subagent_editing_its_own_file_twice_is_rework():
    from lib.trace.session_stats import diagnose_session
    _seed_session("sameagent", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.Edit", {"file_path": "/a.py"}, "e1", "a1"),
        (90, "tool.Edit", {"file_path": "/a.py"}, "e2", "a1"),
    ])
    d = diagnose_session("sameagent")
    rework = next(f for f in d.findings if f.kind == "rework-loop")
    assert rework.minutes == 1.0


def test_fan_out_of_the_same_command_is_not_a_repeat():
    """Ten subagents each running `pytest` once is fan-out, not one agent
    re-running it."""
    from lib.trace.session_stats import diagnose_session
    events = [(0, "prompt", {"text": "go"})]
    events += [(30 * (i + 1), "tool.Bash", {"command_preview": "pytest -q"},
                f"c{i}", f"a{i}") for i in range(4)]
    _seed_session("fanout", events)
    d = diagnose_session("fanout")
    assert not [f for f in d.findings if f.kind == "repeat-command"]


def test_sections_still_partition_attended_time_with_subagents():
    from lib.trace.session_stats import diagnose_session
    _seed_session("partsub", [
        (0, "prompt", {"text": "go"}),
        (30, "tool.Bash", {"command_preview": "ls"}, "b1", "a1"),
        (60, "tool.Edit", {"file_path": "/a.py"}, "e1", "a1"),
        (90, "tool.Edit", {"file_path": "/a.py"}, "e2", "a1"),
    ])
    d = diagnose_session("partsub")
    assert d.attributed_ms + d.remainder_ms == d.attended_ms
