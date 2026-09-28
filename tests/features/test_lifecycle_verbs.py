"""Feature lifecycle verbs (#119 D3, R2): reopen, ship refusals, next_step.

Ship on an escaped feature used to append a second `shipped` with no
`restarted` between them, so the walk-back cost no round. Reopen is the verb
that costs it, and ship is refused wherever it would skip that step.
"""

from __future__ import annotations

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.app import app
from lib.auth import create_token
from lib.features import events, next_step, store
from lib.orm import SessionLocal
from lib.orm.models.features import FeatureEvent
from lib.orm.models.tasks import AgentTask
from tests.tasks.test_slicing import SPEC as SLICEABLE

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
runner = CliRunner()


def _kinds(feature_id: int) -> list[str]:
    with SessionLocal() as session:
        return list(session.exec(
            select(FeatureEvent.kind).where(FeatureEvent.feature_id == feature_id)
            .order_by(FeatureEvent.id)).all())


def _task(feature_id: int, status: str, *, source: str = "test",
          external_id=None) -> int:
    with SessionLocal() as session:
        row = AgentTask(title=f"t {status}", status=status, source=source,
                        external_id=external_id, feature_id=feature_id)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


@pytest.fixture
def escaped(tmp_db, defect_row):
    row = store.create("escaper", body="spec")
    events.append(row.id, events.BUILD_STARTED, declared_by="run:1")
    store.ship(row.id)
    defect_row(row.id)
    assert events.stage(row.id) == events.STAGE_ESCAPED
    return row


def test_feature_reopen_appends_restarted_and_counts_round(escaped,
                                                           flask_client):
    assert events.rounds(escaped.id) == 1

    resp = flask_client.post(f"/api/features/{escaped.id}/reopen",
                             headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["stage"]["stage"] == events.STAGE_BUILDING
    assert body["stage"]["rounds"] == 2
    assert _kinds(escaped.id)[-1] == events.RESTARTED

    # The CLI twin goes through the same store function.
    store.ship(escaped.id)
    result = runner.invoke(app, ["feature", "reopen", str(escaped.id)])
    assert result.exit_code == 0, result.output
    assert "building" in result.output and "rounds=3" in result.output
    assert events.rounds(escaped.id) == 3


def test_feature_reopen_from_abandoned_returns_to_spec(tmp_db, flask_client):
    row = store.create("dropped")
    store.ship(row.id)
    store.abandon(row.id)

    resp = flask_client.post(f"/api/features/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["stage"]["stage"] == events.STAGE_SPEC
    assert events.rounds(row.id) == 1
    assert _kinds(row.id)[-1] == events.SPECCED


@pytest.mark.parametrize("stage", ["spec", "building"])
def test_feature_reopen_refused_before_a_claim(tmp_db, flask_client, stage):
    row = store.create("live")
    if stage == "building":
        events.append(row.id, events.BUILD_STARTED, declared_by="run:1")
    before = _kinds(row.id)

    resp = flask_client.post(f"/api/features/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 409
    assert "nothing to reopen" in resp.get_json()["error"]
    assert _kinds(row.id) == before


def test_feature_ship_refused_when_shipped_escaped_or_abandoned(
        escaped, flask_client):
    shipped = store.create("clean")
    store.ship(shipped.id)
    abandoned = store.create("dropped")
    store.abandon(abandoned.id)

    cases = [(shipped.id, "already shipped"), (escaped.id, "reopen first"),
             (abandoned.id, "reopen first")]
    for feature_id, reason in cases:
        before = _kinds(feature_id)
        resp = flask_client.post(f"/api/features/{feature_id}/ship",
                                 headers=EDITOR)
        assert resp.status_code == 409, feature_id
        assert reason in resp.get_json()["error"]
        with pytest.raises(store.VerbRefused):
            store.ship(feature_id)
        result = runner.invoke(app, ["feature", "ship", str(feature_id)])
        assert result.exit_code == 2
        assert reason in result.output
        assert _kinds(feature_id) == before


def _step(feature_id: int) -> dict:
    return next_step.for_feature(feature_id).to_dict()


def _spec_stage_steps() -> int:
    empty = store.create("no spec")
    assert _step(empty.id)["kind"] == "write_spec"

    prose = store.create("prose", body="## Intent\nsomething")
    step = _step(prose.id)
    assert step["kind"] == "slice"
    assert "Acceptance" in step["label"]

    sliceable = store.create("sliceable", body=SLICEABLE)
    assert _step(sliceable.id) == {
        "kind": "slice", "label": "Transcribe 2 slices",
        "target": f"/features/{sliceable.id}#feature-spec"}
    for n in ("S0", "S1"):
        _task(sliceable.id, "idea", source="spec",
              external_id=f"{sliceable.id}:{n}")
    assert _step(sliceable.id)["label"] == "Arm 2 slices"
    return sliceable.id


def _building_stage_steps(defect_row) -> int:
    building = store.create("building", body="x")
    events.append(building.id, events.BUILD_STARTED, declared_by="run:1")
    assert _step(building.id)["kind"] == "ship"
    defect_row(building.id)
    assert _step(building.id)["kind"] == "resolve_defects"
    armed = _task(building.id, "armed")
    assert _step(building.id) == {"kind": "run", "label": "Run the next task",
                                  "target": f"/tasks/{armed}"}
    running = _task(building.id, "running")
    assert _step(building.id) == {
        "kind": "watch", "label": "A task is running — watch it",
        "target": f"/tasks/{running}"}
    return building.id


def _claimed_stage_steps(escaped_id: int) -> None:
    shipped = store.create("shipped")
    store.ship(shipped.id)
    assert _step(shipped.id)["kind"] == "none"
    assert _step(escaped_id) == {
        "kind": "reopen", "label": "1 blamed defect — reopen to fix",
        "target": f"/features/{escaped_id}"}
    store.abandon(shipped.id)
    assert _step(shipped.id)["kind"] == "reopen"


def test_feature_next_step_per_stage(tmp_db, escaped, defect_row,
                                     flask_client):
    sliceable = _spec_stage_steps()
    building = _building_stage_steps(defect_row)
    _claimed_stage_steps(escaped.id)

    detail = flask_client.get(f"/api/features/{building}",
                              headers=EDITOR).get_json()
    assert detail["next_step"]["kind"] == "watch"
    assert detail["verbs"] == ["ship", "abandon"]
    listed = flask_client.get("/api/features?size=50",
                              headers=EDITOR).get_json()["items"]
    by_id = {row["id"]: row["next_step"] for row in listed}
    assert by_id[building] == detail["next_step"]
    assert by_id[escaped.id]["kind"] == "reopen"
    assert by_id[sliceable]["kind"] == "arm_slices"


def test_ship_check_names_open_work_by_id(tmp_db, defect_row, flask_client):
    row = store.create("building", body="x")
    events.append(row.id, events.BUILD_STARTED, declared_by="run:1")
    armed = _task(row.id, "armed")
    _task(row.id, "done")
    defect_row(row.id)

    check = flask_client.get(f"/api/features/{row.id}",
                             headers=EDITOR).get_json()["ship_check"]

    assert check["allowed"] is True
    assert [t["id"] for t in check["open_tasks"]] == [armed]
    assert len(check["open_defects"]) == 1
    assert check["warnings"] == []


def test_ship_check_warns_a_tracked_feature(tmp_db):
    row = store.create("LIN-1", origin="linear", external_ref="LIN-1")
    events.append(row.id, events.BUILD_STARTED, declared_by="sync:linear")

    check = next_step.ship_check(row.id)

    assert any("still says in progress" in w and "walk-back" in w
               for w in check["warnings"])
