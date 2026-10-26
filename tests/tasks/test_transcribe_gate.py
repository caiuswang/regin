"""Every transcribe entry refuses an unsliceable spec on the same problems the
slices report and a spec write carry (Invariant 9)."""

from __future__ import annotations

import pytest
from sqlmodel import func, select
from typer.testing import CliRunner

from cli.app import app
from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.regin_mcp import features as mcp_features
from tests.spec_fixtures import gherkin, row, spec

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
runner = CliRunner()

UNCOVERED = spec(gherkin("test_a", "test_b"), row("S1", "test_a"))


def _task_count() -> int:
    with SessionLocal() as session:
        return session.exec(select(func.count()).select_from(AgentTask)).one()


def test_transcribe_refuses_unsliceable_spec_on_every_path(tmp_db, flask_client):
    fid = feature_store.create("gated", body=UNCOVERED).id
    before = _task_count()

    cli = runner.invoke(app, ["task", "transcribe", str(fid)])
    assert cli.exit_code == 1
    assert "uncovered test_b" in cli.output

    with pytest.raises(ValueError) as caught:
        mcp_features.feature_transcribe(fid)
    assert "uncovered test_b" in str(caught.value)

    api = flask_client.post(f"/api/features/{fid}/transcribe", headers=EDITOR)
    assert api.status_code == 409
    assert [(p["code"], p["subject"]) for p in api.get_json()["problems"]] == [
        ("uncovered", "test_b")]

    assert _task_count() == before


def test_feature_slices_report_lists_problems(tmp_db, flask_client):
    fid = feature_store.create("reported", body=UNCOVERED).id
    body = flask_client.get(f"/api/features/{fid}/slices").get_json()
    assert body["sliceable"] is False
    assert [p["code"] for p in body["problems"]] == ["uncovered"]
    assert body["problems"][0]["line"] is not None
    assert body["scenarios"] == {"covered": 1, "total": 2}
    assert [r["tests"] for r in body["rows"]] == [["test_a"]]
    assert mcp_features.feature_slices(fid)["problems"] == body["problems"]


def test_spec_write_reports_problems_without_refusing(tmp_db, tmp_path, flask_client):
    fid = feature_store.create("drafted", body="draft").id
    path = tmp_path / "spec.md"
    path.write_text(UNCOVERED)

    cli = runner.invoke(app, ["feature", "spec", str(fid), "--file", str(path)])
    assert cli.exit_code == 0, cli.output
    assert feature_store.require(fid).body == UNCOVERED
    assert "problems: 1" in cli.output
    assert "uncovered test_b" in cli.output

    mcp = mcp_features.feature_set_spec(fid, UNCOVERED)
    assert mcp["problems"]["count"] == 1
    assert mcp["problems"]["first"][0]["subject"] == "test_b"

    api = flask_client.patch(f"/api/features/{fid}", json={"body": UNCOVERED},
                             headers=EDITOR)
    assert api.status_code == 200
    assert api.get_json()["spec_problems"]["count"] == 1


def test_feature_slices_report_marks_fix_tasks(tmp_db, flask_client):
    """A slice row names the defects its task fixes, read from
    `defects.fix_task_id` the way `/api/tasks` rows are (Invariant 5)."""
    from lib.defects import store as defect_store  # noqa: PLC0415

    fid = feature_store.create("fixing", body=spec(gherkin("test_a"), row("S1", "test_a"))).id
    mcp_features.feature_transcribe(fid)
    rows = flask_client.get(f"/api/features/{fid}/slices").get_json()["rows"]
    assert rows[0]["fixes_defects"] == [] and rows[0]["fixes_open_defects"] == []

    task_id = rows[0]["task_id"]
    still_open = defect_store.record("left open", feature_id=fid)
    closed = defect_store.record("already closed", feature_id=fid)
    defect_store.set_fix_item(still_open.id, task_id)
    defect_store.set_fix_item(closed.id, task_id)
    defect_store.close(closed.id, missing_check="test_a")

    row0 = flask_client.get(f"/api/features/{fid}/slices").get_json()["rows"][0]
    assert row0["fixes_defects"] == sorted([still_open.id, closed.id])
    assert row0["fixes_open_defects"] == [still_open.id]
