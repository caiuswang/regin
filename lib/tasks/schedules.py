"""A task's schedule: when an armed attempt may start, or when it starts again.

Two kinds of rule share the one row, and they answer different questions:

  * a **gate** (`at`, `window`) says when the task's armed attempt may start.
    It is ANDed with the dependency check — whichever side is satisfied last
    is the event that launches (`lib/tasks/scheduler.py`).
  * a **recurring** schedule (`cron`) opens a new attempt on every slot.

This module owns the row, the time arithmetic and the fire ledger. It never
launches anything — `scheduler.py` does, through `runs.start_run` — so `runs`
can ask "is this task gated" without an import cycle.

Every time stored here is UTC `YYYY-MM-DDTHH:MM:SSZ`; local wall-clock
interpretation happens only through the row's IANA `tz`.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, AgentTaskSchedule, AgentTaskScheduleFire,
    RUN_STATUS_ARMED, SCHEDULE_GATE_KINDS, SCHEDULE_KIND_AT,
    SCHEDULE_KIND_CRON, SCHEDULE_KIND_WINDOW, SCHEDULE_KINDS,
    TASK_STATUS_ARCHIVED,
)

log = get_activity_logger("tasks")

UPCOMING = 5
CRON_MIN_GAP = timedelta(minutes=5)
_CRON_GAP_PROBE = 10
_ISO = "%Y-%m-%dT%H:%M:%SZ"
_FIELDS = ("kind", "run_at", "window_start", "window_end", "cron_expr", "tz",
           "enabled")

# The scheduler thread waits on this; every schedule write in this process
# sets it, so a new earliest time is picked up without a polling interval.
WAKE = threading.Event()


class ScheduleRejected(ValueError):
    """A schedule write refused with the reason the operator reads (400)."""


class UnknownTask(LookupError):
    """No task with that id (404)."""


def wake() -> None:
    WAKE.set()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_ISO)


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, _ISO).replace(tzinfo=timezone.utc)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def default_tz() -> str:
    """The server's own IANA zone: `$TZ`, else what `/etc/localtime` links to."""
    name = os.environ.get("TZ", "").lstrip(":")
    if name and _zone_or_none(name):
        return name
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    if marker in target:
        name = target.split(marker, 1)[1]
        if _zone_or_none(name):
            return name
    return "UTC"


def _zone_or_none(name: str) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


# ── parsing ──────────────────────────────────────────────────


def _zone(name) -> ZoneInfo:
    zone = _zone_or_none(name) if isinstance(name, str) and name else None
    if zone is None:
        raise ScheduleRejected(f"未知时区 {name!r}，需要 IANA 名称（如 Asia/Shanghai）")
    return zone


def _hhmm(value, field: str) -> str:
    try:
        parsed = datetime.strptime(str(value or "").strip(), "%H:%M")
    except ValueError:
        raise ScheduleRejected(f"{field} 需要 HH:MM 格式") from None
    return parsed.strftime("%H:%M")


def _run_at(value, zone: ZoneInfo) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ScheduleRejected("run_at 需要 ISO8601 时间") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return iso(moment)


def _cron(value) -> str:
    expr = " ".join(str(value or "").split())
    if len(expr.split(" ")) != 5 or not croniter.is_valid(expr):
        raise ScheduleRejected(f"无法解析的 cron 表达式 {expr!r}（需要标准 5 段）")
    return expr


def parse_spec(payload) -> dict:
    """The PUT body as normalised columns. Raises `ScheduleRejected`."""
    if not isinstance(payload, dict):
        raise ScheduleRejected("需要 JSON 对象")
    kind = payload.get("kind")
    if kind not in SCHEDULE_KINDS:
        raise ScheduleRejected(f"kind 必须是 {', '.join(SCHEDULE_KINDS)} 之一")
    tz = payload.get("tz") or default_tz()
    zone = _zone(tz)
    spec = {"kind": kind, "tz": tz, "run_at": None, "window_start": None,
            "window_end": None, "cron_expr": None,
            "enabled": 0 if payload.get("enabled") is False else 1}
    if kind == SCHEDULE_KIND_AT:
        spec["run_at"] = _run_at(payload.get("run_at"), zone)
    elif kind == SCHEDULE_KIND_WINDOW:
        spec["window_start"] = _hhmm(payload.get("window_start"), "window_start")
        spec["window_end"] = _hhmm(payload.get("window_end"), "window_end")
        if spec["window_start"] == spec["window_end"]:
            raise ScheduleRejected("window_start 和 window_end 不能相同")
    else:
        spec["cron_expr"] = _cron(payload.get("cron_expr"))
    return spec


# ── time arithmetic ──────────────────────────────────────────


def _clock(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


def _window_bounds(spec: dict, day: date) -> tuple[datetime, datetime]:
    """The window instance that opens on local `day`, as aware datetimes."""
    zone = ZoneInfo(spec["tz"])
    start_t, end_t = _clock(spec["window_start"]), _clock(spec["window_end"])
    start = datetime.combine(day, start_t, tzinfo=zone)
    end_day = day + timedelta(days=1) if end_t <= start_t else day
    return start, datetime.combine(end_day, end_t, tzinfo=zone)


def window_instance(spec: dict, now: datetime) -> Optional[datetime]:
    """The start of the window instance `now` falls in, or None when closed.

    A window crossing midnight opened on the previous local day, so both
    yesterday's and today's instance are checked.
    """
    today = now.astimezone(ZoneInfo(spec["tz"])).date()
    for day in (today - timedelta(days=1), today):
        start, end = _window_bounds(spec, day)
        if start <= now < end:
            return start
    return None


def window_starts(spec: dict, after: datetime, count: int) -> list[datetime]:
    """The next `count` window openings strictly after `after`."""
    day = after.astimezone(ZoneInfo(spec["tz"])).date() - timedelta(days=1)
    found: list[datetime] = []
    while len(found) < count:
        start, _end = _window_bounds(spec, day)
        if start > after:
            found.append(start)
        day += timedelta(days=1)
    return found


def cron_slots(spec: dict, after: datetime, count: int) -> list[datetime]:
    """The next `count` cron slots strictly after `after`, in the row's tz."""
    it = croniter(spec["cron_expr"], after.astimezone(ZoneInfo(spec["tz"])))
    return [it.get_next(datetime) for _ in range(count)]


def latest_cron_slot(spec: dict, now: datetime) -> datetime:
    """The newest cron slot at or before `now`."""
    probe = now + timedelta(seconds=1)
    it = croniter(spec["cron_expr"], probe.astimezone(ZoneInfo(spec["tz"])))
    return it.get_prev(datetime)


def upcoming(spec: dict, now: datetime, count: int = UPCOMING) -> list[str]:
    if spec["kind"] == SCHEDULE_KIND_AT:
        return [spec["run_at"]]
    if spec["kind"] == SCHEDULE_KIND_WINDOW:
        return [iso(s) for s in window_starts(spec, now, count)]
    return [iso(s) for s in cron_slots(spec, now, count)]


def first_fire(spec: dict, now: datetime) -> str:
    """`next_fire_at` for a freshly written or re-enabled schedule.

    A window that is open right now is due right now (its instance start), so
    an armed, ready task under it starts on the scheduler's next pass rather
    than waiting a day.
    """
    if spec["kind"] == SCHEDULE_KIND_AT:
        return spec["run_at"]
    if spec["kind"] == SCHEDULE_KIND_WINDOW:
        current = window_instance(spec, now)
        if current is not None:
            return iso(current)
    return next_after(spec, now)


def next_after(spec: dict, now: datetime) -> Optional[str]:
    """`next_fire_at` once the slot at `now` has been handled."""
    if spec["kind"] == SCHEDULE_KIND_AT:
        return None
    return upcoming(spec, now, 1)[0]


def gate_open(spec: dict, now: datetime) -> bool:
    if spec["kind"] == SCHEDULE_KIND_AT:
        return now >= parse_iso(spec["run_at"])
    if spec["kind"] == SCHEDULE_KIND_WINDOW:
        return window_instance(spec, now) is not None
    return True


def gate_slot(spec: dict, now: datetime) -> Optional[str]:
    """The fire-ledger slot an open gate launches under, or None when shut."""
    if spec["kind"] == SCHEDULE_KIND_AT:
        return spec["run_at"] if gate_open(spec, now) else None
    current = window_instance(spec, now)
    return iso(current) if current is not None else None


def is_gate(spec: Optional[dict]) -> bool:
    return bool(spec) and spec["enabled"] and spec["kind"] in SCHEDULE_GATE_KINDS


# ── the row ──────────────────────────────────────────────────


def _view(row: AgentTaskSchedule) -> dict:
    return {"id": row.id, "task_id": row.task_id, "kind": row.kind,
            "run_at": row.run_at, "window_start": row.window_start,
            "window_end": row.window_end, "cron_expr": row.cron_expr,
            "tz": row.tz, "enabled": int(row.enabled or 0),
            "next_fire_at": row.next_fire_at, "created_by": row.created_by,
            "created_at": row.created_at, "updated_at": row.updated_at}


def get(task_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.exec(select(AgentTaskSchedule)
                           .where(AgentTaskSchedule.task_id == task_id)).first()
        return _view(row) if row is not None else None


def get_by_id(schedule_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.get(AgentTaskSchedule, schedule_id)
        return _view(row) if row is not None else None


def active_gate(task_id: int) -> Optional[dict]:
    """The task's enabled `at`/`window` schedule, or None."""
    spec = get(task_id)
    return spec if is_gate(spec) else None


def detail(task_id: int, now: Optional[datetime] = None) -> Optional[dict]:
    spec = get(task_id)
    if spec is None:
        return None
    now = now or now_utc()
    return {**spec, "upcoming": upcoming(spec, now),
            "gate_open": gate_open(spec, now)}


def summary(spec: dict, now: datetime) -> dict:
    """What a list row carries about its schedule."""
    if spec["kind"] == SCHEDULE_KIND_AT:
        text = f"不早于 {spec['run_at']}"
    elif spec["kind"] == SCHEDULE_KIND_WINDOW:
        text = f"时间窗 {spec['window_start']}–{spec['window_end']} {spec['tz']}"
    else:
        text = f"cron {spec['cron_expr']} {spec['tz']}"
    return {"kind": spec["kind"], "enabled": spec["enabled"],
            "next_fire_at": spec["next_fire_at"],
            "gate_open": gate_open(spec, now), "summary": text}


def summaries(task_ids: list[int]) -> dict[int, dict]:
    if not task_ids:
        return {}
    now = now_utc()
    with SessionLocal() as session:
        rows = session.exec(select(AgentTaskSchedule).where(
            AgentTaskSchedule.task_id.in_(task_ids))).all()
        specs = [_view(r) for r in rows]
    return {s["task_id"]: summary(s, now) for s in specs}


def list_all() -> list[dict]:
    with SessionLocal() as session:
        rows = session.exec(select(AgentTaskSchedule, AgentTask.title)
                            .join(AgentTask,
                                  AgentTask.id == AgentTaskSchedule.task_id)
                            .order_by(AgentTaskSchedule.task_id)).all()
        return [{**_view(row), "task_title": title} for row, title in rows]


def _has_attempt(session, task_id: int) -> bool:
    return session.exec(select(AgentTaskRun.id)
                        .where(AgentTaskRun.task_id == task_id)).first() is not None


def check_timing(spec: dict, now: datetime) -> None:
    """The refusals decidable from the spec alone, with no task to consult.

    A preview and a write share them: showing an operator a slot they can
    never save is worse than naming the reason up front.
    """
    if spec["kind"] == SCHEDULE_KIND_AT and parse_iso(spec["run_at"]) <= now:
        raise ScheduleRejected("run_at 必须是将来的时间")
    if spec["kind"] == SCHEDULE_KIND_CRON:
        slots = cron_slots(spec, now, _CRON_GAP_PROBE)
        if any(b - a < CRON_MIN_GAP for a, b in zip(slots, slots[1:])):
            raise ScheduleRejected("触发间隔不能小于 5 分钟")


def _refuse(session, task: Optional[AgentTask], spec: dict,
            now: datetime) -> None:
    if task is None:
        raise UnknownTask("unknown task")
    if task.status == TASK_STATUS_ARCHIVED:
        raise ScheduleRejected("已归档的 task 不能定时")
    check_timing(spec, now)
    if spec["kind"] == SCHEDULE_KIND_CRON and not _has_attempt(session,
                                                               task.id):
        raise ScheduleRejected("先 arm 这个 task")


def put(task_id: int, spec: dict, *, user_id: Optional[int] = None,
        now: Optional[datetime] = None) -> dict:
    """Write or replace the task's schedule. Raises `ScheduleRejected` /
    `UnknownTask`. Wakes the scheduler."""
    now = now or now_utc()
    with SessionLocal() as session:
        _refuse(session, session.get(AgentTask, task_id), spec, now)
        row = session.exec(select(AgentTaskSchedule)
                           .where(AgentTaskSchedule.task_id == task_id)).first()
        if row is None:
            row = AgentTaskSchedule(task_id=task_id, created_by=user_id,
                                    created_at=_stamp(), kind=spec["kind"],
                                    tz=spec["tz"])
        for field in _FIELDS:
            setattr(row, field, spec[field])
        row.next_fire_at = first_fire(spec, now) if spec["enabled"] else None
        row.updated_at = _stamp()
        session.add(row)
        session.commit()
        session.refresh(row)
        view = _view(row)
    log.write("task_schedule_set", task_id=task_id, kind=spec["kind"],
              next_fire_at=view["next_fire_at"])
    wake()
    return view


def set_enabled(task_id: int, enabled: bool,
                now: Optional[datetime] = None) -> Optional[dict]:
    """Turn the schedule off, or on with `next_fire_at` recomputed from now."""
    now = now or now_utc()
    with SessionLocal() as session:
        row = session.exec(select(AgentTaskSchedule)
                           .where(AgentTaskSchedule.task_id == task_id)).first()
        if row is None:
            return None
        row.enabled = 1 if enabled else 0
        row.next_fire_at = first_fire(_view(row), now) if enabled else None
        row.updated_at = _stamp()
        session.add(row)
        session.commit()
        session.refresh(row)
        view = _view(row)
    log.write("task_schedule_toggled", task_id=task_id, enabled=enabled)
    wake()
    return view


def remove(task_id: int) -> bool:
    """Delete the schedule and its fire rows. Runs are untouched."""
    with SessionLocal() as session:
        row = session.exec(select(AgentTaskSchedule)
                           .where(AgentTaskSchedule.task_id == task_id)).first()
        if row is None:
            return False
        session.exec(delete(AgentTaskScheduleFire)
                     .where(AgentTaskScheduleFire.schedule_id == row.id))
        session.delete(row)
        session.commit()
    log.write("task_schedule_removed", task_id=task_id)
    wake()
    return True


def set_next(schedule_id: int, next_fire_at: Optional[str], *,
             disable: bool = False) -> None:
    with SessionLocal() as session:
        row = session.get(AgentTaskSchedule, schedule_id)
        if row is None:
            return
        row.next_fire_at = next_fire_at
        if disable:
            row.enabled = 0
        row.updated_at = _stamp()
        session.add(row)
        session.commit()


def due(now: datetime) -> list[dict]:
    """Enabled schedules whose `next_fire_at` has come."""
    with SessionLocal() as session:
        rows = session.exec(select(AgentTaskSchedule).where(
            AgentTaskSchedule.enabled == 1,
            AgentTaskSchedule.next_fire_at.is_not(None),
            AgentTaskSchedule.next_fire_at <= iso(now))
            .order_by(AgentTaskSchedule.next_fire_at)).all()
        return [_view(r) for r in rows]


def earliest() -> Optional[datetime]:
    with SessionLocal() as session:
        first = session.exec(select(AgentTaskSchedule.next_fire_at).where(
            AgentTaskSchedule.enabled == 1,
            AgentTaskSchedule.next_fire_at.is_not(None))
            .order_by(AgentTaskSchedule.next_fire_at)).first()
    return parse_iso(first) if first else None


def is_armed(task_id: int) -> bool:
    with SessionLocal() as session:
        return session.exec(select(AgentTaskRun.id).where(
            AgentTaskRun.task_id == task_id,
            AgentTaskRun.status == RUN_STATUS_ARMED)).first() is not None


# ── the fire ledger ──────────────────────────────────────────


def claim(schedule_id: int, slot_at: str, via: str, outcome: str,
          reason: Optional[str] = None) -> Optional[int]:
    """Insert the slot's one fire row; None when another path already has it."""
    with SessionLocal() as session:
        row = AgentTaskScheduleFire(schedule_id=schedule_id, slot_at=slot_at,
                                    fired_at=_stamp(), outcome=outcome,
                                    via=via, reason=reason)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return None
        return row.id


def settle_fire(fire_id: int, *, outcome: Optional[str] = None,
                run_id: Optional[int] = None,
                reason: Optional[str] = None) -> None:
    with SessionLocal() as session:
        row = session.get(AgentTaskScheduleFire, fire_id)
        if row is None:
            return
        if outcome is not None:
            row.outcome = outcome
        if run_id is not None:
            row.run_id = run_id
        if reason is not None:
            row.reason = reason[:2000]
        session.add(row)
        session.commit()


def _fire_view(row: AgentTaskScheduleFire) -> dict:
    return {"id": row.id, "schedule_id": row.schedule_id,
            "slot_at": row.slot_at, "fired_at": row.fired_at,
            "outcome": row.outcome, "via": row.via, "run_id": row.run_id,
            "reason": row.reason}


def fires(task_id: int, limit: int = 50) -> list[dict]:
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskScheduleFire)
            .join(AgentTaskSchedule,
                  AgentTaskSchedule.id == AgentTaskScheduleFire.schedule_id)
            .where(AgentTaskSchedule.task_id == task_id)
            .order_by(AgentTaskScheduleFire.id.desc())
            .limit(max(1, min(limit, 500)))).all()
        return [_fire_view(r) for r in rows]
