"""What a session row is called and which features it sits under, resolved at
serve time for a whole page at once.

`sessions.title` is never rewritten here: a task's title is the operator's live
assertion and can change after the session ends, so storing it would freeze a
stale copy beside the row it describes.
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import bindparam
from sqlmodel import text

from lib.features.attribution import CANONICAL_TRACE, LANE
from lib.orm import SessionLocal

__all__ = ["canonical_traces", "display_title", "feature_member_traces_sql",
           "features_for_sessions", "tasks_for_sessions"]

#: Sources a person or the agent chose deliberately. Each beats a task title;
#: a first prompt does not.
_CHOSEN_SOURCES = frozenset({"user", "manual", "user_rename",
                             "claude_ai_title", "workflow_name"})


def display_title(title: Optional[str], title_source: Optional[str],
                  task: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """`(display_title, display_title_source)` — rule R1 of F152."""
    if title and title_source in _CHOSEN_SOURCES:
        return title, title_source
    if task and task.get("title"):
        return task["title"], "task"
    if title:
        return title, "first_prompt"
    return None, None


def _ids(trace_ids: Iterable[str]) -> list[str]:
    return [t for t in dict.fromkeys(trace_ids) if t]


def canonical_traces(trace_ids: Iterable[str]) -> dict[str, str]:
    """`{trace_id: canonical trace}` in one query — the bulk `canonical_trace`."""
    ids = _ids(trace_ids)
    out = {t: t for t in ids}
    if not ids:
        return out
    with SessionLocal() as session:
        rows = session.exec(text(
            "SELECT a.trace_id, a.cli_session_id FROM agent_run_sessions a"
            " WHERE a.trace_id IN :ids ORDER BY a.id DESC"
        ).bindparams(bindparam("ids", ids, expanding=True))).all()
    for trace_id, cli in rows:
        out[trace_id] = cli
    return out


def tasks_for_sessions(trace_ids: Iterable[str]) -> dict[str, dict]:
    """`{trace_id: {id, title}}` — the task whose run produced each session.

    Matched on the canonical trace, so the child CLI session a `sdk-*` run
    drove finds its task. Several runs on one session: the newest run wins.
    """
    canon = canonical_traces(trace_ids)
    wanted = _ids(canon.values())
    if not wanted:
        return {}
    with SessionLocal() as session:
        rows = session.exec(text(
            f"SELECT c.trace_id, t.id, t.title FROM ("
            f"  SELECT r.id AS run_id, r.task_id, {CANONICAL_TRACE} AS trace_id"
            f"  FROM agent_task_runs r WHERE r.trace_id IS NOT NULL"
            f") c JOIN agent_tasks t ON t.id = c.task_id"
            f" WHERE c.trace_id IN :ids ORDER BY c.run_id"
        ).bindparams(bindparam("ids", wanted, expanding=True))).all()
    by_canonical = {trace: {"id": tid, "title": title}
                    for trace, tid, title in rows}
    return {t: by_canonical[c] for t, c in canon.items() if c in by_canonical}


def _merged_attributions(session, wanted: list[str]) -> dict[str, dict[int, int]]:
    """`{canonical trace: {feature_id: is_primary}}`, declared ∪ lane."""
    declared = session.exec(text(
        "SELECT sf.trace_id, sf.feature_id, sf.is_primary"
        " FROM session_features sf WHERE sf.trace_id IN :ids"
    ).bindparams(bindparam("ids", wanted, expanding=True))).all()
    lane = session.exec(text(
        f"WITH {LANE} SELECT DISTINCT trace_id, feature_id FROM lane"
        " WHERE trace_id IN :ids"
    ).bindparams(bindparam("ids", wanted, expanding=True))).all()
    merged: dict[str, dict[int, int]] = {}
    for trace, fid, primary in declared:
        merged.setdefault(trace, {})[fid] = int(primary or 0)
    for trace, fid in lane:
        merged.setdefault(trace, {}).setdefault(fid, 1)
    return merged


def _feature_titles(session, fids: list[int]) -> dict[int, str]:
    if not fids:
        return {}
    return dict(session.exec(text(
        "SELECT id, title FROM features WHERE id IN :f"
    ).bindparams(bindparam("f", fids, expanding=True))).all())


def features_for_sessions(trace_ids: Iterable[str]) -> dict[str, list[dict]]:
    """`{trace_id: [{id, title, is_primary}]}`, primary first.

    The bulk twin of `attribution.attributions_for_session`: declared
    `session_features` rows merged with the run lane, a lane-only feature
    reading as primary exactly as it does there.
    """
    canon = canonical_traces(trace_ids)
    wanted = _ids(canon.values())
    if not wanted:
        return {}
    with SessionLocal() as session:
        merged = _merged_attributions(session, wanted)
        titles = _feature_titles(
            session, sorted({f for per in merged.values() for f in per}))
    by_canonical = {
        trace: [{"id": fid, "title": titles.get(fid), "is_primary": bool(p)}
                for fid, p in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))]
        for trace, per in merged.items()}
    return {t: by_canonical[c] for t, c in canon.items() if c in by_canonical}


def feature_member_traces_sql() -> str:
    """SQL selecting every trace attributed to `:feature_id` — the F1 set.

    Both sides of a run/conversation alias are listed so the filter matches
    whichever id the list row carries.
    """
    return (
        f"WITH {LANE}"
        " SELECT trace_id FROM session_features WHERE feature_id = :feature_id"
        " UNION SELECT trace_id FROM lane WHERE feature_id = :feature_id"
        " UNION SELECT r.trace_id FROM agent_task_runs r"
        "  JOIN agent_tasks t ON t.id = r.task_id"
        "  WHERE t.feature_id = :feature_id AND r.trace_id IS NOT NULL")
