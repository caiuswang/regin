"""The ledger's "Referenced by": who cites a feature, on every route that
returns the ledger."""

from __future__ import annotations

from lib.features import store as feature_store
from lib.features import sync as sync_module
from lib.refs.backlinks import referenced_by
from tests.feature_ledger_fixtures import tracker_issue  # noqa: F401
from tests.refs.conftest import EDITOR


def _refs(items: list[dict]) -> list[str]:
    return [item["ref"] for item in items]


def test_backlinks_ignore_code_self_and_own_tasks(feature, task):
    target = feature("Alpha")
    cite = f"[F{target.id} Alpha](feature:{target.id})"
    target_ref = f"feature:{target.id}"

    older = feature("Older", body=f"builds on {cite}")
    newer = feature("Newer", body=f"see {cite} and again {cite}")
    feature("Quoted", body=f"inline `{cite}`\n\n```\n{cite}\n```\n")
    feature("Lookalikes", body=(
        f"![shot](feature:{target.id}) \\[x](feature:{target.id}) "
        f"[x](feature:{target.id}0) [x](feature:{target.id} \"t\")"))
    feature("Unrelated", body="no references here")
    feature_store.set_body(target.id, f"this spec cites itself: {cite}")

    loose = task("Loose", body=f"needs {cite}")
    elsewhere = task("Elsewhere", body=cite, feature_id=older.id)
    task("Own step", body=cite, feature_id=target.id)
    task("Archived", body=cite, status="archived")
    task("Fenced", body=f"~~~\n{cite}\n~~~")

    items = referenced_by(target.id)

    assert _refs(items) == [f"feature:{newer.id}", f"feature:{older.id}",
                            f"task:{elsewhere['id']}", f"task:{loose['id']}"]
    assert target_ref not in _refs(items)
    first = items[0]
    assert first["title"] == "Newer"
    assert first["status"] == "spec"
    assert first["url"] == f"/features/{newer.id}"
    assert items[2]["kind"] == "task"
    assert items[2]["feature_id"] == older.id


def test_ledger_body_carries_referenced_by_on_every_write(
        flask_client, feature, task, tracker_issue):  # noqa: F811
    target = feature("Alpha")
    citing = feature("Cites", body=f"[F{target.id} Alpha](feature:{target.id})")
    step = task("Step", body=f"[F{target.id} x](feature:{target.id})")
    expected = [f"feature:{citing.id}", f"task:{step['id']}"]
    base = f"/api/features/{target.id}"

    answers = {
        "get": flask_client.get(base, headers=EDITOR),
        "patch": flask_client.patch(base, json={"body": "reworded"},
                                    headers=EDITOR),
        "ship": flask_client.post(f"{base}/ship", headers=EDITOR),
        "reopen": flask_client.post(f"{base}/reopen", headers=EDITOR),
        "abandon": flask_client.post(f"{base}/abandon", headers=EDITOR),
    }
    for route, response in answers.items():
        assert response.status_code == 200, (route, response.get_json())
        assert _refs(response.get_json()["referenced_by"]) == expected, route

    tracker_issue("CAI-131", title="remote")
    remote = sync_module.adopt("CAI-131")
    cites_remote = feature("Cites remote",
                           body=f"[F{remote.id} remote](feature:{remote.id})")
    refreshed = flask_client.post(f"/api/features/{remote.id}/refresh",
                                  headers=EDITOR)
    assert refreshed.status_code == 200, refreshed.get_json()
    assert _refs(refreshed.get_json()["referenced_by"]) == [
        f"feature:{cites_remote.id}"]
