"""`PaletteRow` — the one row shape every process entity renders as in the palette.

One builder per kind, each taking a page of ids and answering with a fixed
number of statements however many ids it is handed: a palette page is up to
25 rows, and a builder that asked per row would make the search the slowest
thing on the screen. Nothing here writes.

A session's status is the server phase (`web/blueprints/trace/sessions/phase.py`)
when the caller already holds it — the roster phases every candidate to rank it
— and otherwise a batched reading of the same evidence: `ended`, the runner's
own verdict for a run regin owns, a PENDING human blocker that is its agent's
newest span, and activity age.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from types import SimpleNamespace

from lib.defects.query import TRIAGE_QUEUE
from lib.defects.store import queue_position
from lib.features import events as feature_events
from lib.orm.models.tasks import RUN_STATUS_RUNNING, RUN_STATUS_STARTING
from lib.trace.pending_spans import (
    AGENT_ID_SQL, HUMAN_BLOCKER_NAMES, INACTIVE_THRESHOLD_SEC, PERM_PENDING_PREFIX,
    parse_naive_ts,
)
from lib.trace.trace_service.session_buckets import (
    _skip_slash_invocation, _trim_title,
)
from lib.trace.session_tags import strip_prompt_tag_marker

UNTITLED = "untitled · no prompts"
TITLE_CLIP = 80
CONTEXT_CLIP = 160
# Mirrors `WORKING_WINDOW_SEC` in phase.py — the span-age reading of "working".
WORKING_WINDOW_SEC = 12

NEEDS_YOU = "needs_you"
ACTIVE = "active"
QUIET = "quiet"
DONE = "done"

# R4. `retrying` is a run stalled on the network, not on a human: it reads as
# its own word but buckets with the working, so Home and the roster agree that
# "needs you" means `awaiting` and nothing else.
_SESSION_STATUS = {
    "waiting-permission": ("needs you", NEEDS_YOU),
    "waiting-input": ("needs you", NEEDS_YOU),
    "retrying": ("reconnecting", ACTIVE),
    "working": ("working", ACTIVE),
    "stopping": ("working", ACTIVE),
    "starting": ("working", ACTIVE),
    "idle": ("idle", QUIET),
    "inactive-stale": ("inactive", QUIET),
    "ended": ("ended", DONE),
}
_WAITING_PHASES = ("waiting-permission", "waiting-input")

_TASK_BUCKET = {
    "idea": QUIET, "armed": QUIET, "queued": QUIET, "running": ACTIVE,
    "done": DONE, "failed": DONE, "archived": DONE,
}
_FEATURE_BUCKET = {
    feature_events.STAGE_SPEC: QUIET, feature_events.STAGE_BUILDING: ACTIVE,
    feature_events.STAGE_SHIPPED: DONE, feature_events.STAGE_ESCAPED: DONE,
    feature_events.STAGE_ABANDONED: DONE,
}
_DEFECT_BUCKET = {
    "triage": NEEDS_YOU, "open": ACTIVE, "snoozed": QUIET,
    "closed": DONE, "declined": DONE,
}

# The blockers `roster.py` files under `ask_ts` (→ `waiting-input`); every
# other blocker is a permission request.
_ASK_BLOCKERS = ("tool.AskUserQuestion", "tool.ExitPlanMode")

_SKILL_TOKEN = re.compile(r"^/[a-zA-Z][\w:-]*(\s+|$)")

# What a waiting session is asked, quoted from the blocker's own attributes.
_NEEDS_KEYS = ("question", "prompt", "plan", "description", "command", "tool_name")
_QUESTION_TOOLS = ("AskUserQuestion", "ExitPlanMode")

# Commands whose effect cannot be walked back from the trace: deletes, history
# rewrites, dropped tables. A waiting card flags these red before anyone opens
# the session to answer it.
_DESTRUCTIVE = re.compile(
    r"\brm\s+-[a-zA-Z]*[rRf]"
    r"|\bgit\s+(push\b[^|;&]*\s(--force(-with-lease)?|-f)\b|reset\s+--hard"
    r"|clean\s+-[a-zA-Z]*f|branch\s+-D|checkout\s+--\s)"
    r"|\b(drop|truncate)\s+(table|database|schema)\b"
    r"|\bmkfs\b|\bdd\s+if=",
    re.IGNORECASE)


def _marks(values) -> str:
    return ",".join("?" * len(values))


def _attrs(raw) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(text, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _first_line(text, limit: int = TITLE_CLIP) -> str:
    line = next((ln for ln in str(text or "").splitlines() if ln.strip()), "")
    return _clip(line, limit)


def _prompt_line(text) -> str:
    """A prompt's first line with a leading `/skill` token dropped — a task
    run's every prompt opens with its invocation, which names the skill and
    not the ask. Bare invocations fall through to the next line."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if lines and _SKILL_TOKEN.match(lines[0]):
        rest = _SKILL_TOKEN.sub("", lines[0], count=1).strip()
        lines = [rest, *lines[1:]] if rest else lines[1:]
    return _clip(lines[0], CONTEXT_CLIP) if lines else ""


def _status(word: str, bucket: str) -> dict:
    return {"word": word, "bucket": bucket}


def _chip(kind: str, ident, title: str | None = None) -> dict:
    prefix = {"feature": "F", "task": "T", "defect": "D"}[kind]
    return {"kind": kind, "id": ident, "ref": f"{prefix}{ident}", "title": title or ""}


def _row(kind: str, ref: str, title: str, status: dict, **extra) -> dict:
    row = {"kind": kind, "ref": ref, "title": title, "status": status,
           "owners": extra.pop("owners", [])}
    row.update({k: v for k, v in extra.items() if v})
    return row


def _age(ts, now: datetime) -> str:
    then = parse_naive_ts(ts)
    if then is None:
        return ""
    minutes = max(0, int((now - then).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h"
    return f"{minutes // (24 * 60)}d"


def _owned_phases() -> dict:
    """The live runner's verdict for every run this process owns. Best-effort:
    the SDK tier is optional and a row must not depend on it importing."""
    try:
        from lib import agent_sdk
        return agent_sdk.owned_run_phases()
    except Exception:
        return {}


# ── Session evidence ─────────────────────────────────────────────────


def _agent_activity(conn, trace_ids) -> dict:
    """`{trace: {agent: (last_ts, resolved_count)}}` in one grouped pass."""
    out: dict = {}
    sql = (f"SELECT trace_id, {AGENT_ID_SQL}, MAX(start_time), "
           " SUM(CASE WHEN status_code != 'PENDING' THEN 1 ELSE 0 END) "
           f"FROM session_spans WHERE trace_id IN ({_marks(trace_ids)}) "
           f"GROUP BY trace_id, {AGENT_ID_SQL}")
    for trace_id, agent, last_ts, resolved in conn.execute(sql, list(trace_ids)):
        out.setdefault(trace_id, {})[agent] = (last_ts, resolved or 0)
    return out


def _pending_blockers(conn, trace_ids) -> list:
    sql = (f"SELECT trace_id, {AGENT_ID_SQL}, name, span_id, start_time, attributes "
           "FROM session_spans "
           f"WHERE trace_id IN ({_marks(trace_ids)}) AND status_code = 'PENDING' "
           f"  AND (name IN ({_marks(HUMAN_BLOCKER_NAMES)}) OR span_id LIKE ?) "
           "ORDER BY start_time DESC, id DESC")
    return conn.execute(sql, [*trace_ids, *HUMAN_BLOCKER_NAMES,
                              PERM_PENDING_PREFIX + "%"]).fetchall()


def _parked(activity: dict, blockers: list) -> dict:
    """`{trace: newest blocker row}` for sessions parked on a human.

    A blocker parks its agent only while it is that agent's newest span (a
    later span means it was answered). An agent with no resolved span at all is
    a side fork the harness never ran — its placeholder can never be answered,
    so it parks nothing; the same rule every PENDING reader applies."""
    out: dict = {}
    for row in blockers:
        trace_id, agent, _name, _sid, start, _raw = row
        if trace_id in out:
            continue
        last_ts, resolved = activity.get(trace_id, {}).get(agent, (None, 0))
        if agent and not resolved:
            continue
        if last_ts is None or (start or "") >= last_ts:
            out[trace_id] = row
    return out


def _span_phase(meta: tuple, parked, activity: dict, owned: dict,
                trace_id: str, now: datetime) -> str:
    status = meta[2] if meta else None
    if status == "ended":
        return "ended"
    if trace_id in owned:
        return owned[trace_id]
    if parked:
        is_ask = parked[2] in _ASK_BLOCKERS
        return "waiting-input" if is_ask else "waiting-permission"
    stamps = [ts for ts, _ in activity.get(trace_id, {}).values() if ts]
    return _age_phase(max(stamps) if stamps else None, now)


def session_status(phase: str) -> dict:
    """R4's `{word, bucket}` for a server phase."""
    return _status(*_SESSION_STATUS.get(phase, ("inactive", QUIET)))


def _age_phase(last_ts, now: datetime) -> str:
    last = parse_naive_ts(last_ts)
    if last is None:
        return "idle"
    age = (now - last).total_seconds()
    if age > INACTIVE_THRESHOLD_SEC:
        return "inactive-stale"
    return "working" if age <= WORKING_WINDOW_SEC else "idle"


def session_phases(conn, trace_ids) -> dict:
    """`{trace: phase}` for a page of sessions in a fixed number of statements —
    the batched reading for callers that do not already hold a phase."""
    trace_ids = list(dict.fromkeys(t for t in trace_ids if t))
    if not trace_ids:
        return {}
    meta = _session_meta(conn, trace_ids)
    activity = _agent_activity(conn, trace_ids)
    parked = _parked(activity, _pending_blockers(conn, trace_ids))
    owned, now = _owned_phases(), datetime.now()
    return {t: _span_phase(meta.get(t), parked.get(t), activity, owned, t, now)
            for t in trace_ids}


def _session_meta(conn, trace_ids) -> dict:
    sql = ("SELECT trace_id, title, status, title_source FROM sessions "
           f"WHERE trace_id IN ({_marks(trace_ids)})")
    return {r[0]: tuple(r) for r in conn.execute(sql, list(trace_ids))}


def prompt_text(raw_attributes) -> str:
    return strip_prompt_tag_marker(_attrs(raw_attributes).get("text")) or ""


def _prompts(conn, trace_ids) -> dict:
    """`{trace: (first prompt text, latest prompt text)}` for the main agent."""
    sql = ("SELECT trace_id, attributes, rn_first, rn_last FROM ("
           " SELECT trace_id, attributes, "
           "  ROW_NUMBER() OVER (PARTITION BY trace_id ORDER BY start_time, id) AS rn_first, "
           "  ROW_NUMBER() OVER (PARTITION BY trace_id ORDER BY start_time DESC, id DESC) AS rn_last "
           " FROM session_spans "
           f" WHERE trace_id IN ({_marks(trace_ids)}) AND name = 'prompt' "
           "   AND agent_id IS NULL"
           ") WHERE rn_first = 1 OR rn_last = 1")
    out: dict = {}
    for trace_id, raw, rn_first, rn_last in conn.execute(sql, list(trace_ids)):
        first, latest = out.get(trace_id, (None, None))
        text = prompt_text(raw)
        out[trace_id] = (text if rn_first == 1 else first,
                         text if rn_last == 1 else latest)
    return out


def alias_map(conn, trace_ids) -> dict:
    """`{linked trace id: {page trace ids}}` — each page id plus the other half
    of a regin-launched run (`lib/trace/alias.py`). The roster shows a run
    under its child's id while `agent_task_runs` records the run's own, so a
    link read on one id alone drops the run's owners. Both halves can sit on
    one page, so a linked id may answer for several rows."""
    marks = _marks(trace_ids)
    out: dict = {t: {t} for t in trace_ids}
    for page_id, other in conn.execute(
            f"SELECT cli_session_id, trace_id FROM agent_runs WHERE cli_session_id IN ({marks}) "
            f"UNION SELECT cli_session_id, trace_id FROM agent_run_sessions "
            f" WHERE cli_session_id IN ({marks}) "
            f"UNION SELECT trace_id, cli_session_id FROM agent_runs "
            f" WHERE trace_id IN ({marks}) AND cli_session_id IS NOT NULL",
            [*trace_ids, *trace_ids, *trace_ids]):
        out.setdefault(other, set()).add(page_id)
    return out


def _add_feature(features: dict, trace_id: str, fid, title) -> None:
    chips = features.setdefault(trace_id, [])
    if fid is not None and all(c["id"] != fid for c in chips):
        chips.append(_chip("feature", fid, title))


def session_links(conn, trace_ids) -> tuple[dict, dict]:
    """`({trace: [feature chip…]}, {trace: task chip})` — the declared features
    (primary first) plus the latest task run's task and that task's feature."""
    alias = alias_map(conn, trace_ids)
    marks = _marks(alias)
    features: dict = {}
    for trace_id, fid, title in conn.execute(
            "SELECT sf.trace_id, f.id, f.title FROM session_features sf "
            "JOIN features f ON f.id = sf.feature_id "
            f"WHERE sf.trace_id IN ({marks}) "
            "ORDER BY sf.is_primary DESC, sf.created_at, f.id", list(alias)):
        for page_id in alias[trace_id]:
            _add_feature(features, page_id, fid, title)
    tasks: dict = {}
    for trace_id, tid, title, fid, ftitle in conn.execute(
            "SELECT r.trace_id, t.id, t.title, f.id, f.title "
            "FROM agent_task_runs r JOIN agent_tasks t ON t.id = r.task_id "
            "LEFT JOIN features f ON f.id = t.feature_id "
            f"WHERE r.trace_id IN ({marks}) ORDER BY r.id DESC", list(alias)):
        for page_id in alias[trace_id] - set(tasks):
            tasks[page_id] = _chip("task", tid, title)
            _add_feature(features, page_id, fid, ftitle)
    return features, tasks


def _owner_chips(features: list, task) -> list:
    """`[F, (+N), T]` — the primary feature, how many more, the task."""
    chips = list(features[:1])
    rest = features[1:]
    if rest:
        chips.append({"kind": "more", "id": None, "ref": f"+{len(rest)}",
                      "title": ", ".join(c["ref"] for c in rest)})
    if task:
        chips.append(task)
    return chips


def _session_title(meta, task, prompts) -> str:
    """R3: an explicit title, the owning task's, the first prompt line, or
    the untitled marker — never the id, which `ref` already shows.

    A title ingest derived from the first prompt ranks as the first prompt:
    a task run's first prompt is its skill invocation, which says less about
    the session than the task it runs."""
    title, source = (meta[1], meta[3]) if meta else (None, None)
    if title and source != "first_prompt":
        return title
    if task:
        return task["title"]
    if title:
        return title
    first = _trim_title(_skip_slash_invocation(prompts[0])) if prompts[0] else None
    return first or UNTITLED


def _tool_input(attrs: dict) -> dict:
    raw = attrs.get("tool_input")
    return raw if isinstance(raw, dict) else {}


def _question_text(attrs: dict) -> str:
    """The first question a park asks. The hook tier files it under
    `tool_input.questions`, the SDK tier at the top level."""
    for source in (attrs, _tool_input(attrs)):
        questions = source.get("questions")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            text = questions[0].get("question")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def _command(attrs: dict) -> str:
    for value in (attrs.get("command_preview"), attrs.get("command"),
                  _tool_input(attrs).get("command")):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _needs_text(blocker) -> str:
    attrs = _attrs(blocker[5])
    candidates = (_question_text(attrs), *(attrs.get(k) for k in _NEEDS_KEYS[:4]),
                  _command(attrs), attrs.get("requested_permission"),
                  attrs.get("tool_name"))
    return next((v for v in candidates if isinstance(v, str) and v.strip()),
                blocker[2])


def _is_question(blocker) -> bool:
    """A `permission.request` parked on AskUserQuestion is a question too —
    the hook tier routes every park through that one span name."""
    attrs = _attrs(blocker[5])
    return (blocker[2] in _ASK_BLOCKERS
            or attrs.get("tool_name") in _QUESTION_TOOLS
            or attrs.get("kind") in ("question", "plan"))


def parked_blockers(conn, trace_ids) -> dict:
    """`{trace: newest blocker row}` for the sessions parked on a human."""
    trace_ids = list(dict.fromkeys(t for t in trace_ids if t))
    if not trace_ids:
        return {}
    return _parked(_agent_activity(conn, trace_ids), _pending_blockers(conn, trace_ids))


def blocker_since(blocker):
    return blocker[4]


def waiting_block(blocker) -> dict:
    """What a parked session asks, as text to read — never something to answer
    here. `destructive` marks an approval whose command cannot be undone."""
    question = _is_question(blocker)
    return {
        "kind": "question" if question else "approval",
        "text": _needs_text(blocker),
        "destructive": not question and bool(_DESTRUCTIVE.search(_command(_attrs(blocker[5])))),
        "since": blocker[4],
    }


def _waiting_aside(blocker, now: datetime) -> str:
    kind = "question" if _is_question(blocker) else "approval"
    since = _age(blocker[4], now)
    return f"{kind} · {since}" if since else kind


def session_rows(conn, trace_ids, *, phases: dict | None = None,
                 here: str | None = None) -> dict:
    """`{trace: PaletteRow}` for a page of sessions.

    `phases` is the server verdict when the caller holds it; any trace it does
    not name is phased here, in the same batch."""
    trace_ids = list(dict.fromkeys(t for t in trace_ids if t))
    if not trace_ids:
        return {}
    phases = dict(phases or {})
    meta = _session_meta(conn, trace_ids)
    activity = _agent_activity(conn, trace_ids)
    parked = _parked(activity, _pending_blockers(conn, trace_ids))
    prompts = _prompts(conn, trace_ids)
    features, tasks = session_links(conn, trace_ids)
    owned, now = _owned_phases(), datetime.now()
    out = {}
    for trace_id in trace_ids:
        phase = phases.get(trace_id) or _span_phase(
            meta.get(trace_id), parked.get(trace_id), activity, owned, trace_id, now)
        blocker = parked.get(trace_id) if phase in _WAITING_PHASES else None
        first, latest = prompts.get(trace_id, (None, None))
        context = (f"asks: {_clip(_needs_text(blocker), CONTEXT_CLIP)}" if blocker
                   else _prompt_line(latest))
        out[trace_id] = _row(
            "session", f"#{trace_id[:8]}",
            _session_title(meta.get(trace_id), tasks.get(trace_id), (first, latest)),
            session_status(phase),
            owners=_owner_chips(features.get(trace_id, []), tasks.get(trace_id)),
            aside=_waiting_aside(blocker, now) if blocker else None,
            context=context, here=trace_id == here)
    return out


# ── Task / feature / defect ──────────────────────────────────────────


_OPEN_RUNS = (RUN_STATUS_STARTING, RUN_STATUS_RUNNING)


def _latest_runs(conn, task_ids) -> dict:
    """`{task: [(attempt, run status, trace)…]}`, newest first."""
    out: dict = {}
    for task_id, attempt, status, trace_id in conn.execute(
            "SELECT task_id, attempt, status, trace_id FROM agent_task_runs "
            f"WHERE task_id IN ({_marks(task_ids)}) ORDER BY id DESC",
            list(task_ids)):
        out.setdefault(task_id, []).append((attempt, status, trace_id))
    return out


def _awaiting_traces(conn, runs: dict) -> set:
    """Open-run trace ids parked on a human, phased in one batch. A run regin
    launched is recorded under its `sdk-` id while the blocker lands on the
    child session, so each run is phased through its alias halves too."""
    traces = list(dict.fromkeys(r[2] for rows in runs.values() for r in rows
                                if r[2] and r[1] in _OPEN_RUNS))
    if not traces:
        return set()
    alias = alias_map(conn, traces)
    phases = session_phases(conn, list(alias))
    return {run_id for linked, run_ids in alias.items()
            if phases.get(linked) in _WAITING_PHASES for run_id in run_ids}


def _task_status(status: str, runs: list, awaiting: set) -> dict:
    waiting = status == "running" and any(
        trace in awaiting and run_status in _OPEN_RUNS
        for _attempt, run_status, trace in runs)
    if waiting:
        return _status("needs you", NEEDS_YOU)
    return _status(status, _TASK_BUCKET.get(status, QUIET))


def task_rows(conn, task_ids) -> dict:
    """`{task id: PaletteRow}`. A running task one of whose open runs is parked
    on a human reads `needs you` — the run is where the human has to go."""
    task_ids = list(dict.fromkeys(i for i in task_ids if i is not None))
    if not task_ids:
        return {}
    runs = _latest_runs(conn, task_ids)
    awaiting = _awaiting_traces(conn, runs)
    out = {}
    for tid, title, status, fid, ftitle in conn.execute(
            "SELECT t.id, t.title, t.status, f.id, f.title FROM agent_tasks t "
            "LEFT JOIN features f ON f.id = t.feature_id "
            f"WHERE t.id IN ({_marks(task_ids)})", task_ids):
        task_runs = runs.get(tid, [])
        latest = task_runs[0] if task_runs else None
        out[tid] = _row(
            "task", f"T{tid}", title, _task_status(status, task_runs, awaiting),
            owners=[_chip("feature", fid, ftitle)] if fid is not None else [],
            context=f"run {latest[0]} · {latest[1]}" if latest else None)
    return out


_FEATURE_SQL = f"""
WITH last_event AS (
    SELECT e.feature_id, e.kind,
           (SELECT COUNT(*) FROM defects d WHERE d.blamed_event_id = e.id) AS escapes
    FROM feature_events e
    WHERE e.id = (SELECT MAX(x.id) FROM feature_events x WHERE x.feature_id = e.feature_id)
)
SELECT f.id, f.title,
       {feature_events.stage_sql('le.kind', 'le.escapes')} AS stage,
       1 + (SELECT COUNT(*) FROM feature_events e
            WHERE e.feature_id = f.id AND e.kind = 'restarted') AS rounds,
       (SELECT COUNT(*) FROM agent_tasks t
        WHERE t.feature_id = f.id AND t.status <> 'archived') AS tasks,
       (SELECT COUNT(*) FROM agent_tasks t
        WHERE t.feature_id = f.id AND t.status = 'done') AS done,
       (SELECT COUNT(*) FROM defects d
        WHERE d.feature_id = f.id AND d.status = 'open') AS open_defects
FROM features f LEFT JOIN last_event le ON le.feature_id = f.id
WHERE f.id IN ({{marks}})"""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def feature_rows(conn, feature_ids) -> dict:
    """`{feature id: PaletteRow}`. Progress counts exclude archived tasks."""
    feature_ids = list(dict.fromkeys(i for i in feature_ids if i is not None))
    if not feature_ids:
        return {}
    out = {}
    for fid, title, stage, rounds, tasks, done, open_defects in conn.execute(
            _FEATURE_SQL.format(marks=_marks(feature_ids)), feature_ids):
        word = (f"building · round {rounds}" if stage == feature_events.STAGE_BUILDING
                else stage)
        out[fid] = _row(
            "feature", f"F{fid}", title,
            _status(word, _FEATURE_BUCKET.get(stage, QUIET)),
            context=f"{done}/{tasks} done · {_plural(open_defects, 'open defect')}")
    return out


def _defect_word(row: SimpleNamespace) -> str:
    """R4's one word. The queue is a position the store derives (`triage_state`
    NULL means *not yet routed*, a broken snooze rejoins it), so it is read
    through `queue_position` rather than off the column."""
    if row.status == "closed":
        return "closed"
    position = queue_position(row)
    if position == TRIAGE_QUEUE:
        return "triage"
    return position or "open"


def _defect_detail(symptom, missing_check) -> str:
    lines = [ln for ln in str(symptom or "").splitlines() if ln.strip()]
    return _clip(" ".join(lines[1:]) or missing_check or "", CONTEXT_CLIP)


def defect_rows(conn, defect_ids) -> dict:
    """`{defect id: PaletteRow}`, titled by the symptom's first line."""
    defect_ids = list(dict.fromkeys(i for i in defect_ids if i is not None))
    if not defect_ids:
        return {}
    out = {}
    for (did, symptom, status, triage, watermark, seq, missing, fix_id, own_fid,
         fid, ftitle) in conn.execute(
            "SELECT d.id, d.symptom, d.status, d.triage_state, d.snooze_watermark, "
            "       d.activity_seq, d.missing_check, d.fix_task_id, d.feature_id, "
            "       f.id, f.title "
            "FROM defects d LEFT JOIN features f ON f.id = d.feature_id "
            f"WHERE d.id IN ({_marks(defect_ids)})", defect_ids):
        word = _defect_word(SimpleNamespace(
            status=status, triage_state=triage, feature_id=own_fid,
            snooze_watermark=watermark, activity_seq=seq))
        owner = (_chip("feature", fid, ftitle) if fid is not None
                 else {"kind": "none", "id": None, "ref": "no feature", "title": ""})
        out[did] = _row(
            "defect", f"D{did}", _first_line(symptom) or f"D{did}",
            _status(word, _DEFECT_BUCKET[word]), owners=[owner],
            aside=f"fix T{fix_id}" if fix_id else "no fix",
            context=_defect_detail(symptom, missing))
    return out


_BUILDERS = {
    "session": session_rows, "task": task_rows,
    "feature": feature_rows, "defect": defect_rows,
}


def attach_rows(conn, groups: list[dict]) -> None:
    """Set `row` on every item of a process-entity search group, one builder
    call per group. Items whose entity has vanished keep no `row`."""
    for group in groups:
        build = _BUILDERS.get(group.get("kind"))
        items = group.get("items") or []
        if not build or not items:
            continue
        rows = build(conn, [_item_key(group["kind"], it) for it in items])
        for item in items:
            row = rows.get(_item_key(group["kind"], item))
            if row:
                item["row"] = row


def _item_key(kind: str, item: dict):
    raw = item.get("id")
    if kind == "session":
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
