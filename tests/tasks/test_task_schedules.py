"""F173 — scheduled tasks: `at`/`window` gates ANDed with dependencies, and
`cron` recurrence, all launching through `runs.start_run`.

The clock is `schedules.now_utc`, pinned per test; the launcher is stubbed as
in `test_task_execution.py`, so nothing here starts an agent.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun, AgentTaskScheduleFire
from lib.tasks import runs, scheduler, schedules
from tests.tasks.test_task_execution import (  # noqa: F401
    FakeHandle, _arm, _armed, _capture, _refusing_launch, _run_rows, _task,
    launcher,
)

SH = "Asia/Shanghai"
NIGHT = {"kind": "window", "window_start": "22:00", "window_end": "06:00",
         "tz": SH}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
        timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=_utc("2026-10-24T07:00:00Z"))

    def _set(text: str) -> None:
        state.now = _utc(text)

    state.set = _set
    monkeypatch.setattr(schedules, "now_utc", lambda: state.now)
    return state


def _put(client, task_id, body):
    return client.put(f"/api/tasks/{task_id}/schedule", json=body)


def _fires(task_id) -> list[AgentTaskScheduleFire]:
    sched = schedules.get(task_id)
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTaskScheduleFire)
            .where(AgentTaskScheduleFire.schedule_id == sched["id"])
            .order_by(AgentTaskScheduleFire.id)))


def _chain(client):
    dep = _armed(client, "A")
    task_id = _armed(client, "B")
    client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    return dep, task_id


def _complete(client, launcher, dep):
    assert client.post(f"/api/tasks/{dep}/run").status_code == 201
    launcher.handles[-1].finish()


# ── gate: window ─────────────────────────────────────────────


def test_window_gate_holds_a_dependency_release_until_the_window_opens(
        flask_client, launcher, clock):
    dep, task_id = _chain(flask_client)
    assert _put(flask_client, task_id, NIGHT).status_code == 200
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-24T14:00:00Z"

    _complete(flask_client, launcher, dep)          # local 15:00

    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1

    clock.set("2026-10-24T14:00:00Z")               # local 22:00
    assert scheduler.process_due() == 1

    assert _task(task_id).status == "running"
    [fire] = _fires(task_id)
    assert (fire.outcome, fire.via, fire.slot_at) == (
        "started", "schedule", "2026-10-24T14:00:00Z")
    assert fire.run_id == _run_rows(task_id)[0].id
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-25T14:00:00Z"


def test_dependency_completing_inside_an_open_window_starts_immediately(
        flask_client, launcher, clock):
    dep, task_id = _chain(flask_client)
    _put(flask_client, task_id, NIGHT)
    clock.set("2026-10-24T15:40:00Z")               # local 23:40

    _complete(flask_client, launcher, dep)

    assert _task(task_id).status == "running"
    [fire] = _fires(task_id)
    assert (fire.via, fire.slot_at) == ("dependency", "2026-10-24T14:00:00Z")


def test_window_instance_launches_at_most_once_across_both_paths(
        flask_client, launcher, clock):
    dep, task_id = _chain(flask_client)
    _put(flask_client, task_id, NIGHT)
    clock.set("2026-10-24T15:40:00Z")
    gate = schedules.active_gate(task_id)
    task = runs.store.get_task(task_id)
    # The scheduler's claim lands first; the dependency path then loses it.
    schedules.claim(gate["id"], "2026-10-24T14:00:00Z", "schedule", "started")

    _complete(flask_client, launcher, dep)
    assert scheduler.launch_gated(task, gate, "dependency") is False

    assert len(_fires(task_id)) == 1
    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1


def test_scheduler_at_window_open_waits_for_blocked_dependencies(
        flask_client, launcher, clock):
    dep, task_id = _chain(flask_client)
    _put(flask_client, task_id, NIGHT)
    clock.set("2026-10-24T14:00:00Z")

    scheduler.process_due()

    assert _fires(task_id) == []
    assert _task(task_id).status == "armed"

    clock.set("2026-10-24T16:00:00Z")
    _complete(flask_client, launcher, dep)

    assert _task(task_id).status == "running"
    assert _fires(task_id)[0].via == "dependency"


def test_missed_window_is_not_made_up_in_daytime(flask_client, launcher,
                                                 clock):
    task_id = _armed(flask_client, "nightly")
    clock.set("2026-10-24T13:00:00Z")               # local 21:00
    _put(flask_client, task_id, NIGHT)
    clock.set("2026-10-25T00:00:00Z")               # local 08:00 next day

    scheduler.process_due()

    assert launcher.calls == []
    assert _fires(task_id) == []
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-25T14:00:00Z"


def test_feature_step_can_be_gated(flask_client, feature_row):
    feature_id = feature_row().id
    spec = _capture(flask_client, "spec step")
    build = _capture(flask_client, "build step", kind="build",
                     feature_id=feature_id)
    _arm(flask_client, build)
    flask_client.put(f"/api/tasks/{build}/deps", json={"depends_on": [spec]})

    assert _put(flask_client, build, NIGHT).status_code == 200


# ── gate: at ─────────────────────────────────────────────────


def test_arming_under_an_open_gate_starts_a_task_without_dependencies(
        flask_client, launcher, clock):
    task_id = _capture(flask_client, "solo")
    _put(flask_client, task_id, {"kind": "at",
                                 "run_at": "2026-10-24T08:00:00Z"})
    clock.set("2026-10-24T09:00:00Z")

    _arm(flask_client, task_id)

    assert _task(task_id).status == "running"
    [fire] = _fires(task_id)
    assert fire.via == "arm"
    sched = schedules.get(task_id)
    assert (sched["enabled"], sched["next_fire_at"]) == (0, None)


def test_arming_without_a_gate_keeps_current_behaviour(flask_client,
                                                       launcher):
    task_id = _armed(flask_client, "solo")
    assert _task(task_id).status == "armed"
    assert launcher.calls == []


def test_at_gate_fires_once_at_run_at(flask_client, launcher, clock):
    task_id = _armed(flask_client, "once")
    clock.set("2026-10-23T12:00:00Z")
    assert _put(flask_client, task_id, {
        "kind": "at", "run_at": "2026-10-24T01:00:00Z"}).status_code == 200

    clock.set("2026-10-24T01:00:00Z")
    scheduler.process_due()
    clock.set("2026-10-25T01:00:00Z")
    scheduler.process_due()

    assert len(launcher.calls) == 1
    sched = schedules.get(task_id)
    assert (sched["enabled"], sched["next_fire_at"]) == (0, None)


def test_at_in_the_past_is_refused(flask_client, clock):
    task_id = _capture(flask_client)
    resp = _put(flask_client, task_id, {"kind": "at",
                                        "run_at": "2026-10-24T06:00:00Z"})
    assert resp.status_code == 400
    assert "future" in resp.get_json()["error"]


def test_manual_run_overrides_a_closed_gate(flask_client, launcher, clock):
    task_id = _armed(flask_client)
    _put(flask_client, task_id, NIGHT)              # local 15:00: shut

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 201
    assert _fires(task_id) == []


def test_run_all_skips_a_gated_task(flask_client, launcher, clock):
    free = _armed(flask_client, "free")
    gated = _armed(flask_client, "gated")
    _put(flask_client, gated, NIGHT)

    ok = flask_client.post(f"/api/tasks/{free}/run",
                           json={"respect_schedule": True})
    held = flask_client.post(f"/api/tasks/{gated}/run",
                             json={"respect_schedule": True})

    assert ok.status_code == 201
    assert held.status_code == 409
    body = held.get_json()
    assert body["waiting_schedule"] is True
    assert body["error"].startswith("waiting for schedule")
    assert _task(gated).status == "armed"


def test_refused_launch_is_recorded_and_announced(flask_client, launcher,
                                                  clock, monkeypatch):
    from lib.agent_messages import store as messages

    task_id = _armed(flask_client, "doomed")
    _put(flask_client, task_id, NIGHT)
    monkeypatch.setattr(runs.supervisor, "launch_run", _refusing_launch)
    clock.set("2026-10-24T14:00:00Z")

    scheduler.process_due()

    [fire] = _fires(task_id)
    assert fire.outcome == "refused"
    assert "max_concurrent_runs" in fire.reason
    assert _task(task_id).status == "armed"
    assert messages.live_keyed_message(
        runs.TASK_READY_TRACE, f"schedule-refused:{task_id}") is not None


# ── cron ─────────────────────────────────────────────────────


def test_cron_schedule_fires_a_new_attempt_with_the_task_body_as_prompt(
        flask_client, launcher, clock):
    task_id = _capture(flask_client, "daily digest", body="总结昨天的提交")
    _arm(flask_client, task_id)
    assert _put(flask_client, task_id, {
        "kind": "cron", "cron_expr": "0 9 * * *", "tz": SH}).status_code == 200
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-25T01:00:00Z"

    clock.set("2026-10-25T01:00:00Z")
    scheduler.process_due()

    assert launcher.calls[0]["prompt"].startswith("总结昨天的提交")
    [fire] = _fires(task_id)
    assert fire.outcome == "started"
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-26T01:00:00Z"


def test_cron_runs_again_on_the_next_slot_with_the_previous_params(
        flask_client, launcher, clock, tmp_path):
    task_id = _armed(flask_client, "again", cwd=str(tmp_path))
    _put(flask_client, task_id, {"kind": "cron", "cron_expr": "0 * * * *",
                                 "tz": "UTC"})
    clock.set("2026-10-24T08:00:00Z")
    scheduler.process_due()
    launcher.handles[-1].finish()
    clock.set("2026-10-24T09:00:00Z")
    scheduler.process_due()

    assert [r.attempt for r in _run_rows(task_id)] == [1, 2]
    assert launcher.calls[1]["cwd"] == str(tmp_path)


def test_cron_slot_with_blocked_dependencies_is_skipped(flask_client,
                                                        launcher, clock):
    dep, task_id = _chain(flask_client)
    _put(flask_client, task_id, {"kind": "cron", "cron_expr": "0 * * * *",
                                 "tz": "UTC"})
    clock.set("2026-10-24T08:00:00Z")

    scheduler.process_due()

    [fire] = _fires(task_id)
    assert fire.outcome == "skipped_blocked"
    assert f"#{dep}" in fire.reason
    assert launcher.calls == []


def test_live_previous_attempt_skips_the_cron_slot(flask_client, launcher,
                                                   clock, monkeypatch):
    task_id = _armed(flask_client, "busy")
    _put(flask_client, task_id, {"kind": "cron", "cron_expr": "0 * * * *",
                                 "tz": "UTC"})
    monkeypatch.setattr(runs, "live_run", lambda tid: {"id": 1})
    clock.set("2026-10-24T08:00:00Z")

    scheduler.process_due()

    [fire] = _fires(task_id)
    assert fire.outcome == "skipped_live"
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-24T09:00:00Z"


def test_missed_cron_slots_catch_up_exactly_once(flask_client, launcher,
                                                 clock):
    task_id = _armed(flask_client, "hourly")
    _put(flask_client, task_id, {"kind": "cron", "cron_expr": "0 * * * *",
                                 "tz": "UTC"})
    clock.set("2026-10-24T12:30:00Z")               # 5 slots past 08:00

    scheduler.process_due()

    [fire] = _fires(task_id)
    assert fire.slot_at == "2026-10-24T12:00:00Z"
    assert schedules.get(task_id)["next_fire_at"] == "2026-10-24T13:00:00Z"


def test_too_frequent_cron_is_refused(flask_client):
    task_id = _armed(flask_client)
    resp = _put(flask_client, task_id, {"kind": "cron",
                                        "cron_expr": "*/2 * * * *"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "runs must be at least 5 minutes apart"


def test_cron_needs_an_armed_or_previous_attempt(flask_client):
    task_id = _capture(flask_client)
    resp = _put(flask_client, task_id, {"kind": "cron",
                                        "cron_expr": "0 9 * * *"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "choose launch settings (arm the task) before scheduling repeats"


def test_dst_is_honoured_by_the_schedule_timezone(flask_client):
    resp = flask_client.post("/api/tasks/schedule/preview", json={
        "kind": "cron", "cron_expr": "30 2 * * *", "tz": "America/New_York"})
    upcoming = resp.get_json()["upcoming"]
    assert resp.status_code == 200 and len(upcoming) == 5
    spec = {"kind": "cron", "cron_expr": "30 2 * * *",
            "tz": "America/New_York"}
    before = schedules.cron_slots(spec, _utc("2026-10-30T12:00:00Z"), 1)[0]
    after = schedules.cron_slots(spec, _utc("2026-11-02T12:00:00Z"), 1)[0]
    assert schedules.iso(before) == "2026-10-31T06:30:00Z"     # EDT, -4
    assert schedules.iso(after) == "2026-11-03T07:30:00Z"      # EST, -5


# ── writes and the ledger ────────────────────────────────────


def test_bulk_gate_applies_to_several_tasks(flask_client):
    ids = [_capture(flask_client, f"t{i}") for i in range(3)]
    resp = flask_client.post("/api/tasks/schedule/bulk", json={
        "task_ids": ids + [99999], "schedule": NIGHT})
    body = resp.get_json()
    assert body["applied"] == ids
    assert body["refused"] == [{"id": 99999, "error": "unknown task"}]
    assert all(schedules.get(i)["kind"] == "window" for i in ids)

    cleared = flask_client.post("/api/tasks/schedule/bulk", json={
        "task_ids": ids, "schedule": None}).get_json()
    assert cleared["applied"] == ids
    assert all(schedules.get(i) is None for i in ids)


def test_deleting_the_schedule_keeps_run_history(flask_client, launcher,
                                                 clock):
    task_id = _armed(flask_client, "hourly")
    _put(flask_client, task_id, {"kind": "cron", "cron_expr": "0 * * * *",
                                 "tz": "UTC"})
    for hour in ("08", "09", "10"):
        clock.set(f"2026-10-24T{hour}:00:00Z")
        scheduler.process_due()
        launcher.handles[-1].finish()
    assert len(_fires(task_id)) == 3

    assert flask_client.delete(
        f"/api/tasks/{task_id}/schedule").status_code == 200

    assert len(_run_rows(task_id)) == 3
    with SessionLocal() as session:
        assert session.exec(select(AgentTaskScheduleFire)).all() == []


def test_archiving_disables_and_archived_cannot_be_scheduled(flask_client):
    task_id = _capture(flask_client)
    _put(flask_client, task_id, NIGHT)
    flask_client.patch(f"/api/tasks/{task_id}", json={"status": "archived"})
    assert schedules.get(task_id)["enabled"] == 0

    resp = _put(flask_client, task_id, NIGHT)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "an archived task cannot be scheduled"


def test_get_detail_and_row_summary(flask_client, clock):
    task_id = _armed(flask_client)
    _put(flask_client, task_id, NIGHT)

    detail = flask_client.get(f"/api/tasks/{task_id}/schedule").get_json()
    row = flask_client.get(f"/api/tasks/{task_id}").get_json()["task"]

    assert detail["gate_open"] is False and len(detail["upcoming"]) == 5
    assert row["schedule"]["kind"] == "window"
    assert row["schedule"]["gate_open"] is False
    assert flask_client.get("/api/tasks/424242/schedule").status_code == 404


def test_invalid_specs_are_refused(flask_client):
    task_id = _armed(flask_client)
    for body in ({"kind": "window", "window_start": "22:00",
                  "window_end": "22:00"},
                 {"kind": "window", "window_start": "25:00",
                  "window_end": "06:00"},
                 {"kind": "cron", "cron_expr": "not cron"},
                 {**NIGHT, "tz": "Mars/Olympus"},
                 {"kind": "weekly"}):
        assert _put(flask_client, task_id, body).status_code == 400, body


def test_preview_refuses_what_a_write_refuses(flask_client, clock):
    """A preview that lists a slot the save would turn down is a lie: the
    operator fills the form from it."""
    for body, error in (
            ({"kind": "at", "run_at": "2026-10-24T06:00:00Z"},
             "the start time must be in the future"),
            ({"kind": "cron", "cron_expr": "* * * * *"},
             "runs must be at least 5 minutes apart")):
        resp = flask_client.post("/api/tasks/schedule/preview", json=body)
        assert resp.status_code == 400, body
        assert resp.get_json()["error"] == error

    ok = flask_client.post("/api/tasks/schedule/preview", json={
        "kind": "at", "run_at": "2026-10-24T09:00:00Z"})
    assert ok.status_code == 200
    assert ok.get_json()["upcoming"] == ["2026-10-24T09:00:00Z"]


# ── the thread ───────────────────────────────────────────────


def test_schedule_write_wakes_the_sleeping_scheduler(flask_client, launcher,
                                                     clock):
    """The thread is parked with nothing to wait for (`wait(None)`); a write
    of a schedule already due must be acted on without any timeout firing."""
    task_id = _armed(flask_client, "wake me")
    clock.set("2026-10-24T15:00:00Z")               # local 23:00, window open
    stop = threading.Event()
    thread = threading.Thread(target=scheduler.run_forever, args=(stop,),
                              daemon=True)
    thread.start()

    _put(flask_client, task_id, NIGHT)
    for _ in range(200):
        if _task(task_id).status == "running":
            break
        threading.Event().wait(0.05)
    stop.set()
    schedules.wake()
    thread.join(5)

    assert _task(task_id).status == "running"
    assert _fires(task_id)[0].via == "schedule"


def test_cli_schedule_goes_through_the_server(monkeypatch):
    from typer.testing import CliRunner

    from cli.commands import tasks as task_cli
    from lib.tasks import finish_report

    sent = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"task_id": 7, "kind": "window", "window_start": "22:00",' \
                   b' "window_end": "06:00", "tz": "UTC", "enabled": 1,' \
                   b' "next_fire_at": "2026-10-24T22:00:00Z"}'

    def _open(request, timeout):
        sent.append((request.get_method(), request.full_url,
                     request.data))
        return _Resp()

    monkeypatch.setattr(finish_report, "open_loopback", _open)
    result = CliRunner().invoke(task_cli.tasks_app,
                                ["schedule", "7", "--window", "22:00-06:00"])

    assert result.exit_code == 0, result.output
    method, url, data = sent[0]
    assert method == "PUT" and url.endswith("/api/tasks/7/schedule")
    assert b'"window_start": "22:00"' in data
