"""On-disk handles and readers for Claude Code dynamic-workflow runs."""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lib.providers import get_active_provider


_WF_PREFIX = "wf_"


_PREVIEW_MAX = 240


# read_usage stores per-turn response text; we only need token + tool
# structure, so cap stored text small to bound memory on big transcripts.
_TURN_TEXT_CAP = 4000


@dataclass(frozen=True)
class RunRef:
    """Filesystem handle to one workflow run, live or completed."""

    run_id: str
    session_dir: Path
    journal_path: Path
    agents_dir: Path
    manifest_path: Path
    script_path: Path | None

    def _manifest_mtime(self) -> float | None:
        try:
            return self.manifest_path.stat().st_mtime
        except OSError:
            return None

    def _activity_mtime(self) -> float:
        """Newest mtime across the journal + every agent transcript — the run's
        live edge. Advances as agents stream output (the journal only ticks on
        start/result events) and, crucially, when a paused run is resumed."""
        mtimes = [0.0]
        for p in (self.journal_path, *self.agents_dir.glob("agent-*.jsonl")):
            try:
                mtimes.append(p.stat().st_mtime)
            except OSError:
                continue
        return max(mtimes)

    def snapshot_stale_since(self) -> float | None:
        """mtime of the manifest when it's a *stale* snapshot, else None.

        The manifest is the runtime's progress snapshot, flushed only at
        pause/completion. While a non-``completed`` run keeps progressing (a
        resume that started agents the manifest doesn't list), the rendered tree
        is frozen at that snapshot — phases/counts lag reality. We can't refresh
        it from disk (resume re-issues agents under new ids the snapshot can't
        be reconciled with), so the UI flags it as "snapshot as of <mtime>"
        instead of silently looking current. None when the snapshot still covers
        the live agents (just paused) or the run is ``completed``."""
        manifest = _read_json(self.manifest_path)
        if manifest is None or manifest.get("status") == "completed":
            return None
        _, agent_ids = _manifest_agents(manifest)
        known = set(agent_ids)
        started, _ = _journal_agents(_read_jsonl(self.journal_path))
        if all(a in known for a in started):
            return None                  # snapshot covers the live agents → current
        return self._manifest_mtime()

    @property
    def terminal(self) -> bool:
        """True once a manifest exists — render from it rather than the journal.

        The manifest is the runtime's authoritative per-iteration snapshot,
        written at pause and at completion, carrying the canonical agent set
        with phases / states. It's preferred over the journal whenever it
        exists because the journal is an append log that accumulates *dead*
        agents across pause→resume iterations (re-dispatched work) — a
        journal-driven tree over-counts (e.g. 183 logged vs 100 canonical). The
        journal-only flat tree is the fallback for a run with no manifest yet
        (never paused): a single iteration, so no over-count, and it streams
        live agent work the frozen manifest can't.

        A running run's manifest lags the newest agents (refreshed only at
        pause/completion), but lagging is less wrong than the journal's
        cross-iteration over-count, and `state_mtime` keeps the watcher
        re-ingesting so the tree refreshes the moment the manifest is rewritten.
        """
        return _manifest_status(self.manifest_path) is not None

    def state_mtime(self) -> float:
        """mtime driving the watcher's re-ingest gate. A ``completed`` run is
        stable (manifest mtime); any other state tracks the live edge across the
        manifest + journal + transcripts, so the watcher re-ingests as the
        manifest is rewritten (pause/resume) and as a no-manifest run streams."""
        if _manifest_status(self.manifest_path) == "completed":
            return self._manifest_mtime() or 0.0
        return max(self._manifest_mtime() or 0.0, self._activity_mtime())


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_runs(projects_dir: str | os.PathLike | None = None) -> list[RunRef]:
    """Find every workflow run under the provider's transcript projects dir.

    Anchored on ``journal.jsonl`` (written at run start) so live and
    completed runs are both found. Reuses
    `lib.providers.base.AgentProvider.transcript_projects_dir`.
    """
    if projects_dir is None:
        projects_dir = str(get_active_provider().transcript_projects_dir())
    # projects/<project>/<session>/subagents/workflows/<run_id>/journal.jsonl
    pattern = os.path.join(
        str(projects_dir), "*", "*", "subagents", "workflows",
        f"{_WF_PREFIX}*", "journal.jsonl",
    )
    refs: list[RunRef] = []
    for journal in glob.glob(pattern):
        agents_dir = Path(journal).parent
        run_id = agents_dir.name
        # <S>/subagents/workflows/<run_id> -> <S>
        session_dir = agents_dir.parent.parent.parent
        manifest_path = session_dir / "workflows" / f"{run_id}.json"
        scripts = glob.glob(
            str(session_dir / "workflows" / "scripts" / f"*-{run_id}.js")
        )
        refs.append(RunRef(
            run_id=run_id,
            session_dir=session_dir,
            journal_path=Path(journal),
            agents_dir=agents_dir,
            manifest_path=manifest_path,
            script_path=Path(scripts[0]) if scripts else None,
        ))
    return refs


# --------------------------------------------------------------------------
# small parse / format helpers
# --------------------------------------------------------------------------

def _iso(ms: int | float | None) -> str | None:
    """Epoch milliseconds -> UTC ISO 8601 string (None-safe)."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _manifest_status(path: Path) -> str | None:
    """The manifest's ``status`` (``completed`` / ``killed`` / …), or None when
    the manifest is absent or unreadable. Used to decide whether a run is
    genuinely finished vs paused-and-resumable — see `RunRef.terminal`."""
    manifest = _read_json(path)
    if manifest is None:
        return None
    status = manifest.get("status")
    return status if isinstance(status, str) else "completed"


def _iter_jsonl(path: Path):
    """Yield parsed entries lazily — lets head-only readers (e.g.
    `_agent_full_prompt`) stop after the first hit instead of parsing a whole
    streaming transcript on every live re-ingest poll."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _read_jsonl(path: Path) -> list[dict]:
    return list(_iter_jsonl(path))


def _preview(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value)
    return text[:_PREVIEW_MAX]


def _parent_trace_id(agents_dir: Path) -> str | None:
    """The trace_id of the Claude Code session a run was launched from.

    On-disk a run lives at ``<S>/subagents/workflows/<run_id>``; ``<S>.name``
    is that session's trace_id (and its transcript is the sibling
    ``<S>.jsonl``). Stored on the run root so the run view can offer a
    "↑ launched from session" jump back to its parent.
    """
    try:
        return agents_dir.parent.parent.parent.name or None
    except AttributeError:
        return None


def _first_agent_value(agents_dir: Path, agent_ids: list[str], key: str) -> str | None:
    """Read ``key`` from the first line of the first available agent jsonl.

    Used for ``cwd`` (present on every transcript's first user entry) so
    the run's session row gets tagged to its repo via the normal ingest
    path, and for an accurate live start timestamp.
    """
    for agent_id in agent_ids:
        path = agents_dir / f"agent-{agent_id}.jsonl"
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
        except OSError:
            continue
        if not first:
            continue
        try:
            entry = json.loads(first)
        except ValueError:
            continue
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _manifest_agents(manifest: dict) -> tuple[list[dict], list[str]]:
    """The ``workflow_agent`` progress entries and their agent ids."""
    progress = manifest.get("workflowProgress") or []
    agents = [e for e in progress if e.get("type") == "workflow_agent"]
    return agents, [a["agentId"] for a in agents if a.get("agentId")]


def _journal_agents(events: list[dict]) -> tuple[list[str], dict[str, object]]:
    """(started agent ids in order, {agentId: result}) from journal events."""
    started: list[str] = []
    results: dict[str, object] = {}
    for e in events:
        agent_id = e.get("agentId")
        if not agent_id:
            continue
        if e.get("type") == "started":
            started.append(agent_id)
        elif e.get("type") == "result":
            results[agent_id] = e.get("result")
    return started, results
