"""Which feature key a task holds — the join every defect filed from a task
page is written under (`docs/defect-task-feature.md`).

The rule is a function over two tables, not `'regin:' || id`, and the report
control composed the wrong half of it: a task linked to another tracker is a
*run of* that issue, so its defects belong to the issue's key. Filing them
under `regin:<id>` writes a key the ledger counts against nobody, and the
task's own page — reading the link's key — would never list them back.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskLink
from lib.tasks.links import feature_key_for_task

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _link(task_id: int, key: str, provider: str = "linear") -> None:
    with SessionLocal() as session:
        session.add(AgentTaskLink(task_id=task_id, provider=provider,
                                  external_key=key,
                                  url=f"https://example.test/{key}"))
        session.commit()


def test_a_root_task_with_no_link_is_its_own_feature(native_task):
    task_id = native_task()
    assert feature_key_for_task(task_id) == f"regin:{task_id}"


def test_a_linked_root_task_holds_the_link_key_not_the_native_one(native_task):
    task_id = native_task()
    _link(task_id, "CAI-274")
    assert feature_key_for_task(task_id) == "CAI-274"


def test_a_run_holds_its_parents_key(native_task):
    parent = native_task()
    child = native_task(parent_id=parent)
    assert feature_key_for_task(child, parent) == f"regin:{parent}"


def test_a_run_of_a_linked_feature_holds_the_link_key(native_task):
    parent = native_task()
    _link(parent, "CAI-274")
    child = native_task(parent_id=parent)
    assert feature_key_for_task(child, parent) == "CAI-274"


def test_the_detail_route_carries_the_key_the_page_reports_against(
        flask_client, native_task):
    """The page never composes the key; the route resolves it, so one
    rulebook decides where a report lands."""
    task_id = native_task()
    body = flask_client.get(f"/api/tasks/{task_id}",
                            headers=EDITOR).get_json()
    assert body["feature_key"] == f"regin:{task_id}"

    _link(task_id, "CAI-274")
    body = flask_client.get(f"/api/tasks/{task_id}",
                            headers=EDITOR).get_json()
    assert body["feature_key"] == "CAI-274"


def test_a_defect_reported_from_a_task_page_reads_back_on_its_key(
        flask_client, native_task):
    """The whole round trip the bug broke: file it, then find it."""
    task_id = native_task()
    key = flask_client.get(f"/api/tasks/{task_id}",
                           headers=EDITOR).get_json()["feature_key"]

    opened = flask_client.post("/api/defects", headers=EDITOR,
                               json={"symptom": "the gutter folds",
                                     "feature_key": key})
    assert opened.status_code == 201, opened.get_json()
    defect_id = opened.get_json()["defect"]["id"]

    scoped = flask_client.get(f"/api/defects?feature={key}",
                              headers=EDITOR).get_json()
    every = flask_client.get("/api/defects", headers=EDITOR).get_json()

    assert [i["id"] for i in scoped["items"]] == [defect_id]
    assert defect_id in {i["id"] for i in every["items"]}
