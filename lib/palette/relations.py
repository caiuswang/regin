"""The palette's entity preview (R5): what a feature, task, defect or session
is linked to, the short lists under it, and its body.

Lists hold at most `LIST_CAP` rows plus the uncapped `total`, so "All N →"
names a real count. A parked session's `waiting` block is text to read —
the palette answers nothing (Non-goals); ↵ takes the user to the trace.
Nothing here writes.
"""

from __future__ import annotations

from lib.palette import rows as R
from lib.trace.alias import trace_group
from lib.trace.merge import merge_spans
from lib.trace.projection import _fetch_spans_for_group
from lib.trace.projection_agents import _agent_id_of
from lib.trace.session_tags import strip_prompt_tag_marker

LIST_CAP = 5

# R5 "Tasks (by status)": what is moving first, what is settled last.
_TASK_ORDER = ("running", "queued", "armed", "idea", "failed", "done")
_TASK_RANK_SQL = ("CASE status " + " ".join(
    f"WHEN '{s}' THEN {i}" for i, s in enumerate(_TASK_ORDER))
    + f" ELSE {len(_TASK_ORDER)} END")


def _marks(values) -> str:
    return ",".join("?" * len(values))


def _rel(rel: str, row: dict | None, ident, none: str) -> dict:
    if row is None:
        return {"rel": rel, "target": {"none": none}}
    return {"rel": rel, "target": {"kind": row["kind"], "id": ident,
                                   "ref": row["ref"], "title": row["title"]}}


def _list(label: str, ids: list, rows: dict) -> dict:
    return {"label": label, "total": len(ids),
            "items": [rows[i] for i in ids[:LIST_CAP] if i in rows]}


def _child_of(conn, trace_ids) -> dict:
    """`{trace: the id a human attaches to}` — a regin-launched run's `sdk-`
    id maps to its child's when the child recorded a session."""
    out = {t: t for t in trace_ids}
    if trace_ids:
        out.update(conn.execute(
            "SELECT r.trace_id, r.cli_session_id FROM agent_runs r "
            "JOIN sessions s ON s.trace_id = r.cli_session_id "
            f"WHERE r.trace_id IN ({_marks(trace_ids)})", list(trace_ids)).fetchall())
    return out


def _shown_ids(conn, trace_ids) -> list[str]:
    child = _child_of(conn, trace_ids)
    return list(dict.fromkeys(child[t] for t in trace_ids))


def _recent_sessions(conn, trace_ids) -> list[str]:
    shown = _shown_ids(conn, trace_ids)
    if not shown:
        return []
    return [r[0] for r in conn.execute(
        f"SELECT trace_id FROM sessions WHERE trace_id IN ({_marks(shown)}) "
        "AND is_test = 0 ORDER BY last_seen DESC", shown)]


def _defect_ids(conn, where: str, params: list) -> list[int]:
    return [r[0] for r in conn.execute(
        f"SELECT id FROM defects WHERE {where} ORDER BY opened_at DESC, id DESC",
        params)]


def _feature(conn, fid: int) -> dict | None:
    found = conn.execute("SELECT body FROM features WHERE id = ?", (fid,)).fetchone()
    if not found:
        return None
    task_ids = [r[0] for r in conn.execute(
        "SELECT id FROM agent_tasks WHERE feature_id = ? AND status <> 'archived' "
        f"ORDER BY {_TASK_RANK_SQL}, id DESC", (fid,))]
    done = conn.execute(
        "SELECT COUNT(*) FROM agent_tasks WHERE feature_id = ? AND status = 'done'",
        (fid,)).fetchone()[0]
    defect_ids = _defect_ids(conn, "feature_id = ? AND status = 'open'", [fid])
    session_ids = _recent_sessions(conn, [r[0] for r in conn.execute(
        "SELECT trace_id FROM session_features WHERE feature_id = ? "
        "UNION SELECT r.trace_id FROM agent_task_runs r "
        "JOIN agent_tasks t ON t.id = r.task_id "
        "WHERE t.feature_id = ? AND r.trace_id IS NOT NULL", (fid, fid))])
    return {
        "row": R.feature_rows(conn, [fid]).get(fid),
        "progress": {"done": done, "tasks": len(task_ids),
                     "open_defects": len(defect_ids)},
        "relations": [],
        "lists": [
            _list("Tasks", task_ids, R.task_rows(conn, task_ids[:LIST_CAP])),
            _list("Open defects", defect_ids, R.defect_rows(conn, defect_ids[:LIST_CAP])),
            _list("Recent sessions", session_ids,
                  R.session_rows(conn, session_ids[:LIST_CAP])),
        ],
        "body": found[0] or "",
    }


def _runs_list(conn, tid: int) -> dict:
    """`#id · run N · result`: each run as its session's row, the attempt and
    its result as the aside."""
    runs = conn.execute(
        "SELECT trace_id, attempt, status FROM agent_task_runs "
        "WHERE task_id = ? AND trace_id IS NOT NULL ORDER BY id DESC", (tid,)).fetchall()
    top = runs[:LIST_CAP]
    child = _child_of(conn, [r[0] for r in top])
    rows = R.session_rows(conn, list(child.values()))
    items = [{**rows[child[t]], "aside": f"run {attempt} · {status}"}
             for t, attempt, status in top if child[t] in rows]
    return {"label": "Runs", "total": len(runs), "items": items}


def _task(conn, tid: int) -> dict | None:
    found = conn.execute("SELECT body, feature_id FROM agent_tasks WHERE id = ?",
                         (tid,)).fetchone()
    if not found:
        return None
    body, fid = found
    feature = R.feature_rows(conn, [fid]).get(fid) if fid is not None else None
    fixes = _defect_ids(conn, "fix_task_id = ?", [tid])
    return {
        "row": R.task_rows(conn, [tid]).get(tid),
        "relations": [_rel("Belongs to", feature, fid, "No feature yet")],
        "lists": [_runs_list(conn, tid),
                  _list("Defects it fixes", fixes, R.defect_rows(conn, fixes[:LIST_CAP]))],
        "body": body or "",
    }


def _defect(conn, did: int) -> dict | None:
    found = conn.execute(
        "SELECT symptom, missing_check, feature_id, reported_by_trace, fix_task_id "
        "FROM defects WHERE id = ?", (did,)).fetchone()
    if not found:
        return None
    symptom, missing, fid, reporter, fix_id = found
    reporter = _child_of(conn, [reporter])[reporter] if reporter else None
    feature = R.feature_rows(conn, [fid]).get(fid) if fid is not None else None
    session = R.session_rows(conn, [reporter]).get(reporter) if reporter else None
    fix = R.task_rows(conn, [fix_id]).get(fix_id) if fix_id is not None else None
    return {
        "row": R.defect_rows(conn, [did]).get(did),
        "relations": [
            _rel("Belongs to", feature, fid, "No feature yet"),
            _rel("Reported in", session, reporter, "No reporting session"),
            _rel("Fixed by", fix, fix_id, "No fix task yet"),
        ],
        "lists": [],
        "body": symptom or "",
        "missing_check": missing or "",
    }


def _latest_prompts(conn, group: list[str]) -> list[dict]:
    """The main agent's newest prompts, each with the span id its turn is
    addressed by. Read through the trace view's serve-time merge: the store
    holds a live `promptlive-` placeholder beside each real anchor, and a
    slash command's anchor carries only the echo while the placeholder holds
    the expansion — a raw read lists one turn two or three times."""
    raw = [s for s in _fetch_spans_for_group(conn, group, names=("prompt",))
           if not _agent_id_of(s)]
    out = []
    for span in reversed(merge_spans(raw)):
        text = strip_prompt_tag_marker((span.get("attributes") or {}).get("text"))
        if span.get("name") == "prompt" and not _agent_id_of(span) and text:
            out.append({"span_id": span["span_id"], "ts": span["start_time"], "text": text})
            if len(out) == LIST_CAP:
                break
    return out


def _session_relations(features: list, task) -> list[dict]:
    rels = [{"rel": "Belongs to" if i == 0 else "Also in", "target": chip}
            for i, chip in enumerate(features)]
    if task:
        rels.append({"rel": "Runs task", "target": task})
    return rels or [{"rel": "Belongs to",
                     "target": {"none": "Not linked to a feature or task"}}]


def _session(conn, trace_id: str) -> dict | None:
    group = trace_group(conn, trace_id) or [trace_id]
    if not conn.execute(f"SELECT 1 FROM sessions WHERE trace_id IN ({_marks(group)})",
                        group).fetchone():
        return None
    row = R.session_rows(conn, [trace_id]).get(trace_id)
    features, tasks = R.session_links(conn, [trace_id])
    reported = _defect_ids(conn, f"reported_by_trace IN ({_marks(group)})", group)
    out = {
        "row": row,
        "relations": _session_relations(features.get(trace_id, []), tasks.get(trace_id)),
        "lists": [_list("Defects reported here", reported,
                        R.defect_rows(conn, reported[:LIST_CAP]))],
        "body": "",
        "prompts": _latest_prompts(conn, group),
    }
    if row and row["status"]["bucket"] == R.NEEDS_YOU:
        blockers = R.parked_blockers(conn, group)
        newest = max(blockers.values(), key=R.blocker_since, default=None)
        if newest:
            out["waiting"] = R.waiting_block(newest)
    return out


_READERS = {"feature": _feature, "task": _task, "defect": _defect, "session": _session}
KINDS = tuple(_READERS)


def relations(conn, kind: str, ident) -> dict | None:
    """The preview payload for one entity, or None when it does not exist."""
    reader = _READERS.get(kind)
    if reader is None:
        return None
    if kind != "session":
        try:
            ident = int(ident)
        except (TypeError, ValueError):
            return None
    found = reader(conn, ident)
    return {"kind": kind, "id": ident, **found} if found else None
