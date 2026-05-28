"""`regin logs ...` — view and manage the activity log.

The activity log subsystem (`lib/activity_log.py`) writes one rotating
JSONL stream at `settings.log_dir/regin.log`, with every line tagged
`feature=<name>`. These commands let you inspect it from the terminal.

Subcommands:

  list   — per-feature counts (event count, error count, last seen)
  tail   — print the last N lines, optionally filtered by feature/level
  grep   — regex search across the stream, optionally filtered by feature
  prune  — delete rotated archives older than a cutoff
  path   — print the absolute path to the active log file
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from lib import activity_log
from lib.settings import settings


logs_app = typer.Typer(
    name="logs",
    help="View and manage the regin activity log",
    no_args_is_help=True,
)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_since(raw: str) -> datetime:
    """Parse `1h`, `30m`, `2d`, `45s` into a UTC datetime cutoff."""
    m = _DURATION_RE.match(raw)
    if not m:
        typer.echo(f"invalid --since: {raw!r} (use forms like 1h, 30m, 2d)", err=True)
        raise typer.Exit(2)
    seconds = int(m.group(1)) * _DURATION_UNITS[m.group(2).lower()]
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _format_mtime(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _pretty_record(record: dict) -> str:
    """One-line human view of a loguru-serialized record."""
    rec = record.get("record", {})
    ts = rec.get("time", {}).get("repr", "?")
    level = rec.get("level", {}).get("name", "?")
    message = rec.get("message", "")
    extra = dict(rec.get("extra") or {})
    feature = extra.pop("feature", None)
    extras = " ".join(f"{k}={v!r}" for k, v in extra.items())
    feat_tag = f"[{feature}] " if feature else ""
    return f"{ts}  {level:<7}  {feat_tag}{message}  {extras}".rstrip()


def _read_record(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _ensure_configured() -> None:
    """Configure the sink if we got here outside the normal CLI callback
    path (e.g. unit tests using CliRunner). Idempotent."""
    activity_log.configure_activity_log()


def _record_feature(record: dict) -> str | None:
    return (record.get("record", {}).get("extra") or {}).get("feature")


def _record_level(record: dict) -> str | None:
    return (record.get("record", {}).get("level") or {}).get("name")


def _record_after(record: dict, cutoff: datetime) -> bool:
    ts = record.get("record", {}).get("time", {}).get("timestamp")
    if ts is None:
        return True
    return datetime.fromtimestamp(ts, timezone.utc) >= cutoff


# ── list ────────────────────────────────────────────────────

@logs_app.command("list")
def list_cmd() -> None:
    """Per-feature counts derived from a single pass over `regin.log`."""
    _ensure_configured()
    infos = activity_log.iter_features()
    if not infos:
        typer.echo(f"no activity logs yet (file: {settings.log_dir / 'regin.log'})")
        return
    rows = [
        (i.feature, f"{i.event_count:,}", f"{i.error_count:,}",
         _format_mtime(i.last_seen))
        for i in infos
    ]
    header = ("feature", "events", "errors", "last_seen")
    widths = [
        max(len(row[c]) for row in rows + [header])
        for c in range(len(header))
    ]
    typer.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
    typer.echo("  ".join("-" * w for w in widths))
    for row in rows:
        typer.echo("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


# ── tail ────────────────────────────────────────────────────

@logs_app.command("tail")
def tail_cmd(
    lines: int = typer.Option(50, "-n", "--lines", help="How many lines from the tail"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Stream new lines as they arrive"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Filter by feature tag"),
    level: Optional[str] = typer.Option(None, "--level", help="Filter by log level (DEBUG/INFO/WARNING/ERROR)"),
    raw: bool = typer.Option(False, "--raw", help="Emit JSON lines verbatim (skip pretty-printing)"),
) -> None:
    """Print the last N lines of `regin.log` (optionally filtered)."""
    _ensure_configured()
    path = activity_log.log_path()
    if path is None or not path.exists():
        typer.echo(f"no log yet: {path}", err=True)
        raise typer.Exit(1)
    level_norm = level.upper() if level else None

    def _accept(line: str) -> bool:
        if feature is None and level_norm is None:
            return True
        rec = _read_record(line)
        if rec is None:
            return False
        if feature is not None and _record_feature(rec) != feature:
            return False
        if level_norm is not None and _record_level(rec) != level_norm:
            return False
        return True

    text_lines = path.read_text().splitlines()
    matched: list[str] = [ln for ln in text_lines if _accept(ln)]
    for line in matched[-lines:]:
        _emit_line(line, raw)

    if not follow:
        return
    try:
        with path.open("r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                line = line.rstrip("\n")
                if _accept(line):
                    _emit_line(line, raw)
    except KeyboardInterrupt:
        return


def _emit_line(line: str, raw: bool) -> None:
    if raw:
        typer.echo(line)
        return
    record = _read_record(line)
    if record is None:
        typer.echo(line)
        return
    typer.echo(_pretty_record(record))


# ── grep ────────────────────────────────────────────────────

@logs_app.command("grep")
def grep_cmd(
    pattern: str = typer.Argument(..., help="Python regex"),
    feature: Optional[str] = typer.Option(None, "--feature", help="Limit to records tagged with this feature"),
    ignore_case: bool = typer.Option(False, "-i", "--ignore-case"),
    since: Optional[str] = typer.Option(None, "--since", help="Limit to records newer than 1h / 30m / 2d"),
    raw: bool = typer.Option(False, "--raw"),
) -> None:
    """Regex-search the activity log (optionally filtered by feature)."""
    _ensure_configured()
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        typer.echo(f"invalid regex: {e}", err=True)
        raise typer.Exit(2)
    cutoff = _parse_since(since) if since else None
    path = activity_log.log_path()
    if path is None or not path.exists():
        raise typer.Exit(1)
    matched = 0
    for line in path.read_text().splitlines():
        record = _read_record(line)
        if cutoff and record and not _record_after(record, cutoff):
            continue
        if feature is not None and (record is None or _record_feature(record) != feature):
            continue
        if not rx.search(line):
            continue
        matched += 1
        if raw or record is None:
            typer.echo(line)
        else:
            typer.echo(_pretty_record(record))
    if matched == 0:
        raise typer.Exit(1)


# ── prune ───────────────────────────────────────────────────

@logs_app.command("prune")
def prune_cmd(
    older_than_days: Optional[int] = typer.Option(
        None, "--older-than-days",
        help="Delete rotated files older than this. Default: settings.log_retention_days.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be deleted without deleting"),
) -> None:
    """Delete rotated archives of `regin.log` older than the cutoff.
    The active `regin.log` is never touched."""
    _ensure_configured()
    cutoff = older_than_days if older_than_days is not None else settings.log_retention_days
    deleted = activity_log.prune(older_than_days=cutoff, dry_run=dry_run)
    if not deleted:
        typer.echo(f"nothing to prune (cutoff: {cutoff} days)")
        return
    verb = "would delete" if dry_run else "deleted"
    typer.echo(f"{verb} {len(deleted)} file(s):")
    for path in deleted:
        typer.echo(f"  {path}")


# ── path ────────────────────────────────────────────────────

@logs_app.command("path")
def path_cmd() -> None:
    """Print the absolute path of the active activity log."""
    _ensure_configured()
    path = activity_log.log_path()
    if path is None:
        typer.echo("activity log disabled (REGIN_ACTIVITY_LOG_DISABLED=1)", err=True)
        raise typer.Exit(1)
    typer.echo(str(path))


__all__ = ["logs_app"]
