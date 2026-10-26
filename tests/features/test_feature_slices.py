"""Slicing from the ledger (#119 S3): report, tail, transcribe, arm-proposal
and arm-slices, each over the same `lib/tasks` function its CLI twin calls."""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from cli.app import app
from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_ARMED, TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
)
from lib.tasks import runs

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
runner = CliRunner()

ACCEPTANCE = """## Acceptance

```gherkin
Scenario: test_a
  Then a

Scenario: test_b
  Then b

Scenario: test_c
  Then c
```
"""

HEADER = ("| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | "
          "Skill | Verify |\n|---|---|---|---|---|---|---|---|---|\n")

ROWS = (
    "| S1 | contracts | the contracts | R1 | test_a | — | f9/integration | "
    "goal-verified | pytest tests/a |\n"
    "| S2 | runner | the runner | R2 | test_b | S1 | f9/integration | "
    "goal-verified-treenav | pytest tests/b |\n"
    "| S3 | notice | the notice | R3 | test_c | S1 | f9/integration | — | — |\n"
)

SPEC = ACCEPTANCE + "\n## Work slices\n\n" + HEADER + ROWS


@pytest.fixture
def repo_with_commands(tmp_path):
    root = tmp_path / "proj"
    (root / ".regin").mkdir(parents=True)
    (root / ".regin" / "commands.json").write_text(json.dumps({
        "test": "just spec", "migrate": "./bin/schema up",
        "e2e": "deno task browser", "reasons": {}}))
    with SessionLocal() as session:
        row = Repo(name="proj", path=str(root))
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _feature(body: str = SPEC, project_id=None) -> int:
    return feature_store.create("sliced", body=body, project_id=project_id).id


def _tasks(feature_id: int) -> list[AgentTask]:
    from sqlmodel import select

    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTask).where(AgentTask.feature_id == feature_id)
            .order_by(AgentTask.id)).all())


def _set_status(task_id: int, status: str) -> None:
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        row.status = status
        session.add(row)
        session.commit()


def _report(client, feature_id: int) -> dict:
    return client.get(f"/api/features/{feature_id}/slices").get_json()


def _assert_refusals_are_verbatim(client) -> None:
    prose = _feature("## Intent\nsomething\n\n## Work slices\n\n"
                     + HEADER + ROWS)
    body = _report(client, prose)
    assert body["sliceable"] is False
    assert body["refusal"]["missing"][0].startswith("no_acceptance")
    assert [p["code"] for p in body["problems"]] == ["no_acceptance"]
    # The table is still shown while the spec is fixed.
    assert [r["n"] for r in body["rows"]] == ["S1", "S2", "S3"]

    cyclic = _feature(SPEC.replace("| test_a | — |", "| test_a | S2 |"))
    body = _report(client, cyclic)
    assert body["sliceable"] is False
    assert "cycle" in body["refusal"]["message"]


def _assert_rows_carry_their_tasks(client) -> None:
    fid = _feature()
    body = _report(client, fid)
    assert (body["sliceable"], body["refusal"], body["tail_missing"]) == (
        True, None, True)
    assert body["rows"][1] == {
        "n": "S2", "title": "runner", "depends_on": ["S1"], "tests": ["test_b"],
        "base": "f9/integration", "skill": "goal-verified-treenav",
        "task_id": None, "task_status": None,
        "fixes_defects": [], "fixes_open_defects": []}

    client.post(f"/api/features/{fid}/transcribe", headers=EDITOR)
    ids = [t.id for t in _tasks(fid)]
    assert [(r["n"], r["task_id"], r["task_status"])
            for r in _report(client, fid)["rows"]] == [
        ("S1", ids[0], "idea"), ("S2", ids[1], "idea"), ("S3", ids[2], "idea")]


def test_feature_slices_endpoint_reports_refusal_and_rows(tmp_db, flask_client):
    _assert_refusals_are_verbatim(flask_client)
    _assert_rows_carry_their_tasks(flask_client)
    assert flask_client.get("/api/features/99999/slices").status_code == 404


def test_feature_slices_tail_appends_once(tmp_db, flask_client,
                                          repo_with_commands):
    fid = _feature(project_id=repo_with_commands)

    first = flask_client.post(f"/api/features/{fid}/slices/tail",
                              headers=EDITOR)
    assert first.status_code == 200, first.get_json()
    report = first.get_json()["slices"]
    assert report["tail_missing"] is False
    assert [r["n"] for r in report["rows"]] == [f"S{n}" for n in range(1, 7)]
    # Hung off every leaf, so it waits for both forks.
    assert report["rows"][3]["depends_on"] == ["S2", "S3"]
    assert report["rows"][3]["base"] == f"f{fid}/integration"
    body = feature_store.require(fid).body
    assert "just spec" in body and "./bin/schema up" in body
    assert first.get_json()["ledger"]["body"] == body
    _assert_second_tail_refused(flask_client, fid, body)


def _assert_second_tail_refused(client, fid: int, body: str) -> None:
    again = client.post(f"/api/features/{fid}/slices/tail", headers=EDITOR)
    assert again.status_code == 409
    assert "already ends in a chain tail" in again.get_json()["error"]
    assert feature_store.require(fid).body == body


def test_feature_slices_tail_names_a_command_a_table_cannot_carry(
        tmp_db, flask_client, repo_with_commands):
    with SessionLocal() as session:
        root = session.get(Repo, repo_with_commands).path
    with open(f"{root}/.regin/commands.json", "w") as fh:
        json.dump({"test": "pytest -q 2>&1 | tail -5", "migrate": "m",
                   "e2e": "e", "reasons": {}}, fh)
    fid = _feature(project_id=repo_with_commands)
    resp = flask_client.post(f"/api/features/{fid}/slices/tail",
                             headers=EDITOR)
    assert resp.status_code == 400
    assert "contains a '|'" in resp.get_json()["error"]
    assert feature_store.require(fid).body == SPEC


def test_feature_slices_tail_refuses_a_tracked_spec(tmp_db, flask_client,
                                                    repo_with_commands):
    fid = _feature(project_id=repo_with_commands)
    with SessionLocal() as session:
        from lib.orm.models.features import Feature
        row = session.get(Feature, fid)
        row.origin, row.external_ref = "linear", "LIN-1"
        session.add(row)
        session.commit()
    resp = flask_client.post(f"/api/features/{fid}/slices/tail",
                             headers=EDITOR)
    assert resp.status_code == 409
    assert "tracker" in resp.get_json()["error"]
    assert feature_store.require(fid).body == SPEC


def _shape(slices: list[dict], feature_id: int) -> list[tuple]:
    return [(s["n"], s["action"], s["status"], len(s["deps"]),
             s["arm"]["worktree"]["branch"].replace(f"f{feature_id}/", ""),
             s["arm"]["worktree"]["base"], s["arm"]["skill"]["name"])
            for s in slices]


def _task_shape(feature_id: int) -> list[tuple]:
    return [(t.title, t.status, t.kind) for t in _tasks(feature_id)]


def _assert_retranscribe_names_refusals(client, feature_id, created) -> None:
    _set_status(created[1], TASK_STATUS_RUNNING)
    again = client.post(f"/api/features/{feature_id}/transcribe",
                        headers=EDITOR).get_json()
    assert (again["created"], again["updated"]) == (
        [], [created[0], created[2]])
    assert again["refused"][0]["n"] == "S2"
    assert "running" in again["refused"][0]["reason"]


def test_feature_transcribe_endpoint_matches_cli(tmp_db, flask_client):
    via_api = _feature()
    via_cli = _feature()

    resp = flask_client.post(f"/api/features/{via_api}/transcribe",
                             headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    api_body = resp.get_json()
    result = runner.invoke(app, ["task", "transcribe", str(via_cli), "--json"])
    assert result.exit_code == 0, result.output

    assert len(api_body["created"]) == 3
    assert (api_body["updated"], api_body["refused"]) == ([], [])
    assert _shape(api_body["slices"], via_api) == _shape(
        json.loads(result.output)["slices"], via_cli)
    assert _task_shape(via_api) == _task_shape(via_cli)

    _assert_retranscribe_names_refusals(flask_client, via_api,
                                        api_body["created"])

    empty = _feature("")
    resp = flask_client.post(f"/api/features/{empty}/transcribe",
                             headers=EDITOR)
    assert resp.status_code == 409 and "no spec" in resp.get_json()["error"]


def test_task_arm_proposal_from_slice_row(tmp_db, flask_client):
    fid = _feature()
    created = flask_client.post(f"/api/features/{fid}/transcribe",
                                headers=EDITOR).get_json()["created"]
    s1, _, s3 = created

    body = flask_client.get(f"/api/tasks/{s1}/arm-proposal",
                            headers=EDITOR).get_json()
    assert body == {"branch": f"f{fid}/s1", "base": "f9/integration",
                    "skill": "goal-verified"}
    # An empty Skill cell proposes the column default.
    s3_body = flask_client.get(f"/api/tasks/{s3}/arm-proposal",
                               headers=EDITOR).get_json()
    assert s3_body["skill"]

    # Derived at read time: an edit to the table moves the proposal.
    feature_store.set_body(fid, SPEC.replace("goal-verified | pytest tests/a",
                                             "tdd | pytest tests/a"))
    assert flask_client.get(f"/api/tasks/{s1}/arm-proposal",
                            headers=EDITOR).get_json()["skill"] == "tdd"

    # A row the table dropped, and a task that is no slice, have none.
    feature_store.set_body(fid, SPEC.replace(ROWS.splitlines()[0] + "\n", ""))
    gone = flask_client.get(f"/api/tasks/{s1}/arm-proposal", headers=EDITOR)
    assert gone.status_code == 404 and "no longer" in gone.get_json()["error"]
    plain = flask_client.post("/api/tasks", json={"title": "plain"},
                              headers=EDITOR).get_json()
    plain_id = plain.get("id") or plain["task"]["id"]
    assert flask_client.get(f"/api/tasks/{plain_id}/arm-proposal",
                            headers=EDITOR).status_code == 404


def _arm(client, feature_id: int, items: list) -> dict:
    return client.post(f"/api/features/{feature_id}/arm-slices",
                       headers=EDITOR, json={"items": items})


@pytest.fixture
def base_repo(tmp_path):
    """A checkout that has `f9/integration`, the base the table names —
    arm refuses a base git cannot resolve."""
    root = tmp_path / "base-repo"
    root.mkdir()
    for args in (["-c", "init.defaultBranch=main", "init"],
                 ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "T"],
                 ["commit", "--allow-empty", "-m", "init"],
                 ["branch", "f9/integration"]):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    return root


def _assert_first_arm(client, fid, s1, s2, s3, foreign, cwd) -> None:
    resp = _arm(client, fid, [
        {"task_id": s1, "cwd": str(cwd),
         "worktree": {"branch": f"f{fid}/s1", "base": "f9/integration"},
         "skill": {"name": "goal-verified"}},
        {"task_id": s2, "bogus": 1},
        {"task_id": s3},
        {"task_id": foreign},
    ])
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["armed"] == [s1]
    reasons = {r["task_id"]: r["reason"] for r in body["refused"]}
    assert "unknown field: bogus" in reasons[s2]
    assert "S3 is armed" in reasons[s3]
    assert f"not a slice of feature #{fid}" in reasons[foreign]


def test_arm_slices_bulk_names_refusals_and_never_runs(tmp_db, flask_client,
                                                       monkeypatch, base_repo):
    started = []
    monkeypatch.setattr(runs, "start_run", started.append)
    fid, other = _feature(), _feature()
    s1, s2, s3 = flask_client.post(f"/api/features/{fid}/transcribe",
                                   headers=EDITOR).get_json()["created"]
    foreign = flask_client.post(f"/api/features/{other}/transcribe",
                                headers=EDITOR).get_json()["created"][0]
    _set_status(s3, TASK_STATUS_ARMED)

    _assert_first_arm(flask_client, fid, s1, s2, s3, foreign, base_repo)

    assert {t.id: t.status for t in _tasks(fid)} == {
        s1: TASK_STATUS_ARMED, s2: TASK_STATUS_IDEA, s3: TASK_STATUS_ARMED}
    pending = runs.armed_attempt(s1)
    assert pending["params"]["worktree"] == {"branch": f"f{fid}/s1",
                                             "base": "f9/integration"}

    # S2 waits on S1, which is armed but not done: arming it launches nothing.
    assert _arm(flask_client, fid, [{"task_id": s2}]).get_json() == {
        "armed": [s2], "refused": []}
    assert started == []
    assert _arm(flask_client, fid, []).status_code == 400
