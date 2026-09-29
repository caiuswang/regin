"""Capture Claude Code dynamic-workflow runs as regin session/span trees.

A *dynamic workflow* (the Claude Code `Workflow` tool) runs many subagents
in a background runtime across named *phases*. Its agents never reach
regin's hooks, but the runtime persists the whole run on disk. This module
reads those artifacts and projects each run onto regin's existing
OTel-style span store (``sessions`` + ``session_spans``) with **zero new
schema**, so a workflow run renders in the normal trace UI as
``run -> phase -> agent -> turn``.

On-disk layout, per run ``wf_<id>`` inside a Claude session dir ``<S>``::

    <S>/workflows/wf_<id>.json                 run manifest (written at completion)
    <S>/workflows/scripts/<name>-wf_<id>.js    the script (written at start)
    <S>/subagents/workflows/wf_<id>/
        journal.jsonl                          started/result events (written live)
        agent-<agentId>.jsonl  + .meta.json    each agent's transcript

The manifest is the runtime's authoritative per-iteration snapshot, written at
pause and at completion (not continuously while running, and not at start). It
carries the canonical agent set with phases / labels / states — and crucially
de-duplicates pause→resume *iterations*, where the journal (an append log)
keeps the dead agents of superseded iterations. So capture prefers the
manifest whenever it exists; the journal-only tree is the fallback for a run
that has never paused. See `ingest_run` / `RunRef.terminal`:

* **manifest exists** -> `build_full_spans`: the full ``phase -> agent -> turn``
  tree (real phases, labels, per-agent state, deep per-turn / per-tool spans).
  A live ``running`` snapshot stays open (no ``session.end``); ``completed`` /
  paused ``killed`` close it. A running run's snapshot can lag the newest
  agents (it refreshes only at pause/completion), but that's less wrong than
  the journal's cross-iteration over-count.
* **no manifest yet** (never paused) -> `build_flat_spans`: a flat run + agent
  list keyed off the journal, status ``running``, agents deep-expanded (so a
  live run streams agent work) and — when the script declares them statically
  — grouped under script-derived phases.

Span ids are deterministic (``wfrun-`` / ``wfphase-`` / ``wfagent-`` /
``wfturn-`` / ``wftool-``), so re-ingesting a run is idempotent. `reingest`
clears a run's rows before re-inserting, so the flat tree is cleanly replaced
by the manifest tree once the first snapshot is written.
"""

from __future__ import annotations

import time

from lib.activity_log import get_activity_logger
from lib.trace.workflow_run_files import (
    RunRef,
    _iso,
    _journal_agents,
    _manifest_agents,
    _read_json,
    _read_jsonl,
    discover_runs,
)
from lib.trace.workflow_spans import (
    _agent_turn_spans,
    build_flat_spans,
    build_full_spans,
)
from lib.trace.workflow_session_links import (
    _clear_run,
    _session_token_split,
    _set_session_cost,
    _set_session_origin,
    _set_session_title,
    _set_session_tokens,
    _stamp_parent_link,
    _tag_workflow_sessions,
)
from lib.trace.workflow_script_meta import (
    _load_script_meta,
    _parse_script_agents,
    _parse_script_meta,
    _parse_script_phases,
)

log = get_activity_logger("trace_ingest")


def reingest(run_id: str, spans: list[dict]) -> tuple[int, int]:
    """Clear then re-insert a run's spans via the shared ingest service.

    Returns ``(ingested, skipped)`` from
    `lib.trace.trace_service.ingest_session_spans`.
    """
    from lib.trace.trace_service import ingest_session_spans

    _clear_run(run_id)
    normalised = [(span, span["attributes"]) for span in spans]
    return ingest_session_spans(normalised)


def ingest_run(run_ref: RunRef, *, deep: bool = True,
               is_test: bool = False) -> tuple[int, int] | None:
    """Ingest one run: the manifest's full phase tree when it covers the live
    agent set (`RunRef.terminal`), else the journal-driven flat live tree."""
    if run_ref.terminal:
        manifest = _read_json(run_ref.manifest_path)
        if manifest is None or not manifest.get("runId"):
            return None
        stale = run_ref.snapshot_stale_since()
        spans = build_full_spans(
            manifest, run_ref.agents_dir, deep=deep, is_test=is_test,
            snapshot_stale_at=_iso(int(stale * 1000)) if stale else None)
        result = reingest(run_ref.run_id, spans)
        _, agent_ids = _manifest_agents(manifest)
        _set_session_tokens(run_ref.run_id,
                            _session_token_split(run_ref.agents_dir, agent_ids))
        _set_session_cost(run_ref.run_id, run_ref.agents_dir, agent_ids)
        name = manifest.get("workflowName")
        _set_session_title(run_ref.run_id, name or manifest.get("summary"))
        _set_session_origin(run_ref.run_id)
        _tag_workflow_sessions(run_ref.run_id, run_ref.session_dir.name)
        _stamp_parent_link(run_ref, name, agent_ids)
        return result
    spans = build_flat_spans(run_ref, deep=deep, is_test=is_test)
    result = reingest(run_ref.run_id, spans)
    started, _ = _journal_agents(_read_jsonl(run_ref.journal_path))
    _set_session_tokens(run_ref.run_id,
                        _session_token_split(run_ref.agents_dir, started))
    _set_session_cost(run_ref.run_id, run_ref.agents_dir, started)
    name = _parse_script_meta(run_ref.script_path)[0]
    _set_session_title(run_ref.run_id, name)
    _set_session_origin(run_ref.run_id)
    _tag_workflow_sessions(run_ref.run_id, run_ref.session_dir.name)
    _stamp_parent_link(run_ref, name, started)
    return result


def ingest_all(*, deep: bool = True) -> dict:
    """Ingest every discoverable run once. Returns a small summary dict."""
    summary = {"runs": 0, "spans": 0, "failed": 0}
    for run_ref in discover_runs():
        try:
            result = ingest_run(run_ref, deep=deep)
        except Exception:
            summary["failed"] += 1
            log.error("workflow_run_ingest_failed", exc_info=True,
                      run_id=run_ref.run_id)
            continue
        if result is not None:
            summary["runs"] += 1
            summary["spans"] += result[0]
            log.write("workflow_run_ingested", run_id=run_ref.run_id,
                      terminal=run_ref.terminal, spans=result[0])
    return summary


def watch(poll_seconds: float = 5.0, *, deep: bool = True, stop=None) -> None:
    """Poll for new/changed runs and (re)ingest them until ``stop`` is set.

    Re-ingest is gated on state mtime + terminal-ness so an unchanged run
    is skipped: a live run re-ingests (flat) only when its journal grows,
    and the one-time deep parse happens on the single completion pass.
    Intended to run in a daemon thread started by ``regin serve``.
    """
    seen: dict[str, tuple[float, bool]] = {}
    while stop is None or not stop.is_set():
        for run_ref in discover_runs():
            mtime = run_ref.state_mtime()
            if seen.get(run_ref.run_id) == (mtime, run_ref.terminal):
                continue
            try:
                ingest_run(run_ref, deep=deep)
                seen[run_ref.run_id] = (mtime, run_ref.terminal)
            except Exception:
                log.error("workflow_run_ingest_failed", exc_info=True,
                          run_id=run_ref.run_id)
        if stop is not None:
            stop.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)
