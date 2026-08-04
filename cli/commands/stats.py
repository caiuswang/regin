"""`regin stats` — engineering-time analytics over recorded sessions.

Reports where build time actually goes, so the follow-up question ("what
would make us faster?") can be answered from data instead of intuition. The
metric layer lives in `lib.trace.session_stats`; this module is presentation
plus the JSON/CSV export used for deeper analysis elsewhere.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional, Sequence

import typer

from cli import output
from cli.deps import require_db

stats_app = typer.Typer(
    name="stats",
    help="Engineering-time analytics over real (non-test) sessions",
    no_args_is_help=True,
)

_SINCE = typer.Option(None, "--since", help="Only sessions started on/after this date (YYYY-MM-DD)")
_UNTIL = typer.Option(None, "--until", help="Only sessions started before this date (YYYY-MM-DD)")
_PROJECT = typer.Option(None, "--project", help="Substring match against the session cwd")
_ORIGIN = typer.Option("session", "--origin", help="Comma-separated origins to include (session, workflow, …)")
_INCLUDE_TEST = typer.Option(False, "--include-test", help="Include sessions flagged is_test")
_MIN_MINUTES = typer.Option(1.0, "--min-minutes", help="Drop sessions with less engaged time than this")
_FORMAT = typer.Option("table", "--format", "-f", help="table | json | csv")
_OUT = typer.Option(None, "--out", "-o", help="Write to this file instead of stdout")


@stats_app.callback()
def _main() -> None:
    """Keeps `stats` a command group rather than a bare command."""


def _build_filter(since, until, project, origin, include_test,
                  min_minutes=1.0):
    from lib.trace.session_stats import StatsFilter
    origins = tuple(o.strip() for o in (origin or "session").split(",") if o.strip())
    if not origins:
        # An empty IN-list renders as a false predicate: every command would
        # print an empty table with no hint that the filter was the problem.
        output.error("--origin must name at least one origin (e.g. session)")
        raise typer.Exit(2)
    return StatsFilter(
        since=since, until=until, project=project, origins=origins,
        include_test=include_test, min_engaged_ms=int(min_minutes * 60_000),
    )


def _hours(ms: Optional[int]) -> str:
    return f"{(ms or 0) / 3_600_000:.1f}"


def _pct(part: float, whole: float) -> str:
    return f"{(100.0 * part / whole):.0f}%" if whole else "-"


def _emit(payload: Any, fmt: str, out: Optional[str],
          rows: Optional[Sequence[dict]] = None,
          render=None) -> None:
    """Render `payload` in the requested format, to stdout or a file."""
    if fmt not in ("table", "json", "csv"):
        output.error(f"Unknown format {fmt!r}; expected table|json|csv")
        raise typer.Exit(2)
    if fmt == "json":
        body = json.dumps(payload, indent=2, default=str)
    elif fmt == "csv":
        body = _to_csv(rows if rows is not None else payload)
    else:
        buf = io.StringIO()
        _with_sink(buf, render)
        body = buf.getvalue()
    if out:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(body if body.endswith("\n") else body + "\n")
        except OSError as exc:
            output.error(f"Could not write {out}: {exc.strerror or exc}")
            raise typer.Exit(1) from exc
        output.echo(f"wrote {out}")
    else:
        output.echo(body, end="" if body.endswith("\n") else "\n")


def _with_sink(buf: io.StringIO, render) -> None:
    """Run `render` with the output helpers pointed at `buf`.

    Table rendering goes through `cli.output`, whose sink is a module
    attribute; capturing it keeps `--out` working for every format without
    each renderer needing to know where its bytes end up.
    """
    prev = output._stdout
    output._stdout = buf
    try:
        render()
    finally:
        output._stdout = prev


def _to_csv(rows: Sequence[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _load(flt):
    from lib.trace.session_stats import engaged_windows, load_sessions, summarize
    sessions = load_sessions(flt)
    totals = summarize(sessions, engaged_windows(flt))
    return sessions, totals


def _render_headline(totals) -> None:
    hours = totals.calendar_ms / 3_600_000
    output.table([
        ["Calendar time (deduped)", _hours(totals.calendar_ms), "wall time with parallel sessions counted once"],
        ["Attended time", _hours(totals.engaged_ms), f"sum over sessions; {totals.concurrency:.2f}x parallel"],
        ["  ├─ agent + harness", _hours(totals.agent_ms), _pct(totals.agent_ms, totals.engaged_ms) + " of attended"],
        ["  └─ waiting on a human", _hours(totals.human_ms), _pct(totals.human_ms, totals.engaged_ms) + " — you typing, deciding, approving"],
        ["Active days", str(totals.days), f"{hours / totals.days:.1f} h/day" if totals.days else "-"],
        ["Sessions", str(totals.sessions), f"{totals.prompts} prompts"],
        ["Edits", str(totals.edits), f"{_pct(totals.rework_edits, totals.edits)} are re-edits of a file already touched"],
        ["Tool failures", str(totals.failures), f"{totals.failures / totals.sessions:.1f} per session" if totals.sessions else "-"],
        ["Cost", f"${totals.cost_usd:,.0f}", f"${totals.cost_usd / hours:,.0f}/engaged hour" if hours else "-"],
    ], headers=["metric", "value", "note"])


@stats_app.command("time", help="Headline report: where build time goes")
@require_db
def cmd_time(since: Optional[str] = _SINCE, until: Optional[str] = _UNTIL,
             project: Optional[str] = _PROJECT, origin: str = _ORIGIN,
             include_test: bool = _INCLUDE_TEST,
             min_minutes: float = _MIN_MINUTES,
             fmt: str = _FORMAT, out: Optional[str] = _OUT) -> None:
    from lib.trace.session_stats import load_activity, load_commands
    flt = _build_filter(since, until, project, origin, include_test, min_minutes)
    sessions, totals = _load(flt)
    if not sessions:
        output.error("No sessions matched the filter.")
        raise typer.Exit(1)
    activity = load_activity(flt)
    commands = load_commands(flt)
    payload = {
        "totals": {**totals.__dict__, "concurrency": totals.concurrency,
                   "agent_share": totals.agent_share},
        "activity": activity, "shell": commands,
    }

    def _render() -> None:
        _render_headline(totals)
        output.echo("\nAgent time by activity")
        output.table(
            [[r["activity"], _hours(r["ms"]), _pct(r["ms"], totals.active_ms), f"{r['events']:,}"]
             for r in activity],
            headers=["activity", "hours", "share", "events"])
        output.echo("\nShell time by command intent")
        output.table(
            [[r["command"], _hours(r["ms"]), _pct(r["ms"], totals.active_ms), f"{r['calls']:,}"]
             for r in commands],
            headers=["command", "hours", "share", "calls"])

    _emit(payload, fmt, out, rows=activity, render=_render)


@stats_app.command("sessions", help="Per-session rows — the export for further analysis")
@require_db
def cmd_sessions(since: Optional[str] = _SINCE, until: Optional[str] = _UNTIL,
                 project: Optional[str] = _PROJECT, origin: str = _ORIGIN,
                 include_test: bool = _INCLUDE_TEST,
                 min_minutes: float = _MIN_MINUTES,
                 limit: int = typer.Option(30, "--limit", help="Rows shown in table format (0 = all)"),
                 fmt: str = _FORMAT, out: Optional[str] = _OUT) -> None:
    flt = _build_filter(since, until, project, origin, include_test, min_minutes)
    from lib.trace.session_stats import load_sessions
    sessions = sorted(load_sessions(flt), key=lambda s: -s.engaged_ms)
    rows = [s.as_row() for s in sessions]
    shown = sessions[:limit] if limit else sessions

    def _render() -> None:
        output.table(
            [[s.day, s.trace_id[:8], (s.title or "")[:44], s.project,
              _hours(s.engaged_ms), _hours(s.agent_ms), s.prompts, s.edits,
              s.rework_edits, s.failures, f"{s.cost_usd:.2f}"] for s in shown],
            headers=["day", "trace", "title", "project", "engaged_h",
                     "active_h", "prompts", "edits", "rework", "fails", "usd"])
        if limit and len(sessions) > limit:
            output.echo(f"\n… {len(sessions) - limit} more; use --limit 0 or --format csv")

    _emit(rows, fmt, out, rows=rows, render=_render)


@stats_app.command("daily", help="Deduped calendar hours per day")
@require_db
def cmd_daily(since: Optional[str] = _SINCE, until: Optional[str] = _UNTIL,
              project: Optional[str] = _PROJECT, origin: str = _ORIGIN,
              include_test: bool = _INCLUDE_TEST,
              fmt: str = _FORMAT, out: Optional[str] = _OUT) -> None:
    flt = _build_filter(since, until, project, origin, include_test)
    sessions, totals = _load(flt)
    by_day = {r["day"]: r for r in _group(sessions, "day")}
    rows = [{"day": day, "calendar_h": round(ms / 3_600_000, 2),
             "attended_h": round(by_day.get(day, {}).get("engaged_ms", 0) / 3_600_000, 2),
             "agent_h": round(by_day.get(day, {}).get("agent_ms", 0) / 3_600_000, 2),
             "sessions": by_day.get(day, {}).get("sessions", 0),
             "edits": by_day.get(day, {}).get("edits", 0),
             "cost_usd": round(by_day.get(day, {}).get("cost_usd", 0.0), 2)}
            for day, ms in totals.per_day_ms.items()]

    def _render() -> None:
        output.table(
            [[r["day"], f'{r["calendar_h"]:.1f}', f'{r["attended_h"]:.1f}',
              f'{r["agent_h"]:.1f}', r["sessions"], r["edits"], f'{r["cost_usd"]:.2f}']
            for r in rows],
            headers=["day", "calendar_h", "attended_h", "agent_h", "sessions",
                     "edits", "usd"])

    _emit(rows, fmt, out, rows=rows, render=_render)


def _mins(ms: Optional[int]) -> str:
    return f"{(ms or 0) / 60_000:.1f}"


def _detail_header(d) -> None:
    s = d.stat
    output.echo(f"{s.trace_id}  {s.title or '(untitled)'}")
    output.echo(f"{s.started_at[:19]} → {s.last_seen[:19]}   "
                f"{s.model or '?'}   {s.project}\n")
    output.table([
        ["Attended", _hours(s.engaged_ms) + " h", f"{s.prompts} requests"],
        ["  agent + harness", _hours(s.agent_ms) + " h", _pct(s.agent_ms, s.engaged_ms) + " of attended"],
        ["  waiting on a human", _hours(s.human_ms) + " h", _pct(s.human_ms, s.engaged_ms) + " — you typing, deciding, approving"],
        ["Edits", str(s.edits), f"{s.files_touched} files, {s.rework_edits} re-edits "
                                f"({_pct(s.rework_edits, s.edits)})"],
        ["Tool failures", str(s.failures), f"${s.cost_usd:.2f} spent"],
    ], headers=["metric", "value", "note"])


def _detail_body(d) -> None:
    total = d.stat.active_ms
    output.echo("\nTime by request (a request is one user prompt "
                "and everything before the next)")
    output.table(
        [[f"#{s.index}", s.started_at[11:19], _mins(s.engaged_ms), s.edits,
          s.failures, s.tools, s.top_activity, s.prompt[:58]]
         for s in sorted(d.segments, key=lambda x: -x.engaged_ms)],
        headers=["req", "at", "min", "edits", "fails", "tools",
                 "top activity", "prompt"])
    output.echo("\nTime by activity")
    output.table([[r["activity"], _mins(r["ms"]), _pct(r["ms"], total)]
                  for r in d.activity], headers=["activity", "min", "share"])
    output.echo("\nMost-edited files (re-edit churn is the usual cause of a long session)")
    output.table([[r["edits"], r["file"]] for r in d.files[:12]],
                 headers=["edits", "file"])
    output.echo("\nSlowest single steps")
    output.table([[f'{r["ms"] / 1000:.0f}s', r["label"]] for r in d.slowest],
                 headers=["took", "step"])
    if d.failures:
        by_tool: dict[str, int] = {}
        for f in d.failures:
            by_tool[f["tool"]] = by_tool.get(f["tool"], 0) + 1
        output.echo("\nFailures by tool")
        output.table(sorted(by_tool.items(), key=lambda kv: -kv[1]),
                     headers=["tool", "count"])


def _render_diagnosis(d) -> None:
    s = d.stat
    output.echo(f"{s.trace_id}  {s.title or '(untitled)'}")
    output.echo(f"{s.started_at[:19]} → {s.last_seen[:19]}   "
                f"{s.model or '?'}   {s.project}\n")
    output.echo(f"  {s.engaged_ms / 3_600_000:.1f} h attended  "
                f"({s.agent_ms / 3_600_000:.1f} h agent+harness, "
                f"{s.human_ms / 3_600_000:.1f} h waiting on you)   "
                f"{s.edits} edits   {s.failures} failures   ${s.cost_usd:.2f}\n")
    g = d.delegation
    if g.agents:
        output.echo(f"  {g.agents} subagents did {g.subagent_ms / 3_600_000:.1f} h "
                    f"of the {g.total_ms / 3_600_000:.1f} h agent-hours "
                    f"({g.delegated_share * 100:.0f}%), run "
                    f"{g.parallelism:.2f}x parallel against the wall clock\n")
    output.echo(f"  VERDICT: {d.verdict}\n")
    for gap in d.blind_spots:
        output.echo(f"  BLIND SPOT: {gap}")
    if d.blind_spots:
        output.echo("")
    if not d.findings:
        return
    total_m = d.attended_ms / 60_000
    output.echo(f"WASTE — {d.attributed_ms / 60_000:.0f} of {total_m:.0f} min "
                f"({d.attributed_share * 100:.0f}%):")
    for f in d.findings:
        output.echo(f"  {f.minutes:5.0f}m  {f.headline}")
        for line in f.detail:
            output.echo(f"          · {line}")
    output.echo(f"\nFORWARD WORK — {d.remainder_ms / 60_000:.0f} min "
                f"({(1 - d.attributed_share) * 100:.0f}%), the cost of doing "
                f"it once:")
    for r in d.remainder:
        output.echo(f"  {r['ms'] / 60_000:5.0f}m  {r['group']}")
    output.echo("\nEvery gap is awarded to exactly one line, so the two "
                "sections sum to attended time.")


@stats_app.command("session", help="Why one session took as long as it did")
@require_db
def cmd_session(trace_id: str = typer.Argument(..., help="Session trace_id (prefix is enough)"),
                breakdown: bool = typer.Option(
                    False, "--breakdown",
                    help="Also show the per-request / activity / file tables"),
                fmt: str = _FORMAT, out: Optional[str] = _OUT) -> None:
    from dataclasses import asdict

    from lib.trace.session_stats import (
        diagnose_session, resolve_trace_id, session_detail)
    resolved = resolve_trace_id(trace_id)
    if not resolved:
        output.error(f"No session matches {trace_id!r}")
        raise typer.Exit(1)
    if len(resolved) > 1:
        output.error(f"{trace_id!r} is ambiguous: {', '.join(resolved[:5])}")
        raise typer.Exit(2)
    diag = diagnose_session(resolved[0])
    if diag is None:
        output.error(f"No spans recorded for {resolved[0]}")
        raise typer.Exit(1)
    detail = session_detail(resolved[0]) if breakdown else None
    payload = {
        "session": diag.stat.as_row(),
        "verdict": diag.verdict,
        "attributed_ms": diag.attributed_ms,
        "remainder_ms": diag.remainder_ms,
        "attended_ms": diag.attended_ms,
        "delegation": asdict(diag.delegation),
        "remainder": diag.remainder,
        "blind_spots": diag.blind_spots,
        "findings": [f.as_row() for f in diag.findings],
    }
    if detail is not None:
        payload.update(segments=[asdict(s) for s in detail.segments],
                       activity=detail.activity, files=detail.files,
                       slowest=detail.slowest, failures=detail.failures)

    def _render() -> None:
        _render_diagnosis(diag)
        if detail is not None:
            _detail_body(detail)

    _emit(payload, fmt, out, rows=[f.as_row() for f in diag.findings],
          render=_render)


def _group(sessions, key):
    from lib.trace.session_stats import group_by
    return group_by(sessions, key)


@stats_app.command("by", help="Aggregate sessions by day, project, model, or agent_type")
@require_db
def cmd_by(dimension: str = typer.Argument(..., help="day | project | model | agent_type"),
           since: Optional[str] = _SINCE, until: Optional[str] = _UNTIL,
           project: Optional[str] = _PROJECT, origin: str = _ORIGIN,
           include_test: bool = _INCLUDE_TEST,
           fmt: str = _FORMAT, out: Optional[str] = _OUT) -> None:
    if dimension not in ("day", "project", "model", "agent_type"):
        output.error(f"Unknown dimension {dimension!r}; expected day|project|model|agent_type")
        raise typer.Exit(2)
    flt = _build_filter(since, until, project, origin, include_test)
    from lib.trace.session_stats import load_sessions
    rows = _group(load_sessions(flt), dimension)

    def _render() -> None:
        output.table(
            [[r[dimension][:40], r["sessions"], _hours(r["engaged_ms"]),
              _hours(r["agent_ms"]), _hours(r["human_ms"]), r["prompts"],
              r["edits"], r["rework_edits"], r["failures"], f'{r["cost_usd"]:.2f}']
             for r in rows],
            headers=[dimension, "sessions", "attended_h", "agent_h", "human_h",
                     "prompts", "edits", "rework", "fails", "usd"])

    _emit(rows, fmt, out, rows=rows, render=_render)


__all__ = ["stats_app"]
