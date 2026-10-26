"""Feature #167 Rules B — a task arm that names no `one_shot` auto-closes.

Left open, a task run idles on a `max_concurrent_runs` slot after its agent
reports, so the default is the one that gives the slot back. Every arm path —
the single-task route, the feature's slice arm, and the CLI — parses through
`arm.parse_arm`, which is where the default lives.
"""

from __future__ import annotations

import urllib.error

from typer.testing import CliRunner

from cli.app import app
from lib.auth import create_token
from lib.features import store as feature_store
from lib.tasks import runs

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}

SPEC = """## Acceptance

```gherkin
Scenario: test_a
  Then a

Scenario: test_b
  Then b
```

## Work slices

| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|
| S1 | contracts | the contracts | R1 | test_a | — | f9/integration | — | — |
| S2 | runner | the runner | R2 | test_b | — | f9/integration | — | — |
"""


def _capture(client) -> int:
    resp = client.post("/api/tasks", json={"title": "a task"})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _armed_params(client, **body) -> dict:
    task_id = _capture(client)
    resp = client.post(f"/api/tasks/{task_id}/arm", json=body)
    assert resp.status_code == 200, resp.get_json()
    return runs.armed_attempt(task_id)["params"]


def test_arm_without_one_shot_defaults_to_auto_close(flask_client):
    assert _armed_params(flask_client)["one_shot"] is True


def test_explicit_one_shot_false_is_kept(flask_client):
    assert _armed_params(flask_client, one_shot=False)["one_shot"] is False


def test_slice_arm_defaults_to_auto_close(flask_client, monkeypatch):
    monkeypatch.setattr(runs, "start_run", lambda task_id: None)
    fid = feature_store.create("sliced", body=SPEC).id
    created = flask_client.post(f"/api/features/{fid}/transcribe",
                                headers=EDITOR).get_json()["created"]

    resp = flask_client.post(f"/api/features/{fid}/arm-slices", headers=EDITOR,
                             json={"items": [{"task_id": t} for t in created]})

    assert resp.get_json() == {"armed": created, "refused": []}
    assert [runs.armed_attempt(t)["params"]["one_shot"] for t in created] == [
        True, True]


def test_cli_arm_defaults_to_auto_close(flask_client, monkeypatch):
    def _server_down(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr("cli.commands.tasks._open_loopback", _server_down)
    task_id = _capture(flask_client)

    result = CliRunner().invoke(app, ["task", "arm", str(task_id)])

    assert result.exit_code == 0, result.output
    assert runs.armed_attempt(task_id)["params"]["one_shot"] is True
