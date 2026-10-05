"""The "Referenced by" lists: who cites a feature, on every route that
returns the ledger, and who cites a task, defect or session, on its page."""

from __future__ import annotations

from lib.features import store as feature_store
from lib.features import sync as sync_module
from lib.refs.backlinks import referenced_by
from lib.tasks import store as task_store
from tests.feature_ledger_fixtures import tracker_issue  # noqa: F401
from tests.refs.conftest import ADMIN, ANON, EDITOR, VIEWER


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

    items = referenced_by(target_ref)

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


def test_backlinks_list_citers_of_tasks_defects_and_sessions(
        feature, task, defect):
    parent = feature("Parent")
    step = task("Step", body="no references", feature_id=parent.id)
    bug = defect("Delta broke", feature_id=parent.id)
    sid = "3fd8137e-e0fd-4a5b-9c1d-0123456789ab"
    cite_step = f"[T{step['id']} Step](task:{step['id']})"
    cite_bug = f"[D{bug.id} Delta broke](defect:{bug.id})"
    cite_session = f"[S3fd8137e x](session:{sid})"
    cite_file = "[docs/a b.md](file:docs/a%20b.md)"

    # A step's own feature citing it is a citation like any other; the step
    # citing itself is not.
    feature_store.set_body(parent.id, f"slices: {cite_step}")
    task_store.update_task(step["id"], {"body": f"see {cite_step}"})
    spec = feature("Spec", body=f"{cite_bug} and {cite_session}")
    feature("Quoted", body=f"`{cite_step}` `{cite_bug}`")
    note = task("Note", body=f"{cite_step} {cite_session} {cite_file}")
    task("Old", body=cite_bug, status="archived")

    assert _refs(referenced_by(f"task:{step['id']}")) == [
        f"feature:{parent.id}", f"task:{note['id']}"]
    assert _refs(referenced_by(f"defect:{bug.id}")) == [f"feature:{spec.id}"]
    assert _refs(referenced_by(f"session:{sid}")) == [
        f"feature:{spec.id}", f"task:{note['id']}"]
    assert _refs(referenced_by("file:docs/a%20b.md")) == [
        f"task:{note['id']}"]
    assert referenced_by("defect:999999") == []


def test_backlinks_route_refuses_bad_refs_and_non_admin_sessions(
        flask_client, feature):
    sid = "3fd8137e-e0fd-4a5b-9c1d-0123456789ab"
    citer = feature("Citer", body=f"[S3fd8137e x](session:{sid})")
    session_url = f"/api/refs/backlinks?ref=session:{sid}"

    assert flask_client.get(session_url, headers=ANON).status_code == 401
    for headers in (EDITOR, VIEWER):
        refused = flask_client.get(session_url, headers=headers)
        assert (refused.status_code, refused.get_json()["field"]) \
            == (403, "ref")
    admin = flask_client.get(session_url, headers=ADMIN)
    assert (admin.status_code, _refs(admin.get_json()["items"])) \
        == (200, [f"feature:{citer.id}"])

    viewer = flask_client.get(f"/api/refs/backlinks?ref=feature:{citer.id}",
                              headers=VIEWER)
    assert (viewer.status_code, viewer.get_json()) \
        == (200, {"ref": f"feature:{citer.id}", "items": []})
    for bad in ("", "feature:0", "feature:x", "nope:1", "file:../a"):
        answer = flask_client.get(f"/api/refs/backlinks?ref={bad}",
                                  headers=EDITOR)
        assert (answer.status_code, answer.get_json()["field"]) \
            == (400, "ref"), bad
