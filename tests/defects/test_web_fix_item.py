"""Closing the defect→fix loop from the browser.

`0` of 63 defects carried a `fix_task_id` and `0` tasks had `source='defect'`
when this was written: the CLI verb existed, the read surface rendered its
result, and nothing in a browser could ever write one. These are the
assertions that the loop now runs end to end from a page.
"""

from __future__ import annotations

import pathlib

from sqlmodel import select

from lib.auth import create_token
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}

ROOT = pathlib.Path(__file__).resolve().parents[2]
ACTIONS = ROOT / "frontend/src/components/defects/DefectActions.vue"


def test_creating_a_fix_task_from_the_browser_closes_the_loop(flask_client,
                                                              feature_row):
    """One post writes both sides: the pointer on the defect and a task the
    board can open, filed under the feature that caused the bug."""
    feature = feature_row("the feature")
    row = store.record("the gutter folds the newest span",
                       feature_id=feature.id, session_id="t-1",
                       suspect_paths=["lib/trace/merge.py"])
    store.close(row.id, missing_check="test_the_gutter_keeps_the_newest_span")

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"create": True}, headers=EDITOR)

    assert answer.status_code == 200, answer.get_json()
    task_id = answer.get_json()["defect"]["fix_task_id"]
    assert task_id == store.get(row.id).fix_task_id
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
    assert task.source == "defect"
    assert task.feature_id == feature.id
    assert task.title == "the gutter folds the newest span"


def test_the_fix_task_body_cites_the_named_check_rather_than_copying_it(
        flask_client, feature_row):
    """The check reaches the run through the defect's reference line, read at
    launch, so a body cut before the defect closed is not left without it."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id, session_id="t-9")
    store.close(row.id, missing_check="test_it_does_not_break")

    flask_client.post(f"/api/defects/{row.id}/fix-item",
                      json={"create": True}, headers=EDITOR)

    with SessionLocal() as session:
        task = session.get(AgentTask, store.get(row.id).fix_task_id)
    assert f"](defect:{row.id})" in task.body
    assert "test_it_does_not_break" not in task.body
    assert "t-9" not in task.body


def test_an_open_defect_names_no_check_and_the_body_says_nothing(
        flask_client, feature_row):
    """An unclosed defect has no claim to carry, and an empty `missing check:`
    line would read as one that was named and left blank."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    flask_client.post(f"/api/defects/{row.id}/fix-item",
                      json={"create": True}, headers=EDITOR)

    with SessionLocal() as session:
        task = session.get(AgentTask, store.get(row.id).fix_task_id)
    assert "missing check" not in task.body


def test_pointing_at_an_existing_task(flask_client, feature_row, native_task):
    feature = feature_row()
    task_id = native_task(title="the fix")
    row = store.record("it broke", feature_id=feature.id)

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"task_id": task_id}, headers=EDITOR)

    assert answer.status_code == 200
    assert store.get(row.id).fix_task_id == task_id


def test_a_task_id_that_names_no_task_is_refused(flask_client, feature_row):
    """The pointer is a foreign key, so a typo is checkable and is checked."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"task_id": 999999}, headers=EDITOR)

    assert answer.status_code == 409
    assert store.get(row.id).fix_task_id is None


def test_creating_under_an_unattributed_defect_is_refused(flask_client):
    """A task no feature owns is the orphan late-binding attribution exists to
    avoid — and the route states the store's own remedy."""
    row = store.record("it broke", session_id="t-1")

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"create": True}, headers=EDITOR)

    assert answer.status_code == 409
    assert "regin defects attribute" in answer.get_json()["error"]
    assert store.get(row.id).fix_task_id is None


def test_naming_both_or_neither_is_a_bad_request(flask_client, feature_row,
                                                 native_task):
    """Two different assertions. A body naming both has no single answer, and
    one naming neither is a no-op the caller would read as a write."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    task_id = native_task(title="the fix")

    for payload in ({}, {"create": True, "task_id": task_id}):
        answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                                   json=payload, headers=EDITOR)
        assert answer.status_code == 400, payload
    assert store.get(row.id).fix_task_id is None


def test_a_hostile_body_is_refused_rather_than_written(flask_client,
                                                       feature_row,
                                                       native_task):
    """Each of these reached the foreign key or the JSON reader before it was
    checked. `isinstance(True, int)` is the sharp one: `{"task_id": true}`
    would have pointed the defect at task 1."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    native_task(title="task one")

    for payload, why in (
            ([1, 2], "a JSON array has no fields to read"),
            ("hi", "nor does a bare string"),
            ({"task_id": True}, "a bool is an int in python, not a task id"),
            ({"task_id": 0}, "ids start at one"),
            ({"task_id": "5"}, "a string reaches the foreign key as one"),
            ({"create": "false"}, "a non-empty string is not a flag"),
            ({"create": 1}, "nor is a number")):
        answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                                   json=payload, headers=EDITOR)
        assert answer.status_code == 400, (payload, answer.get_json())
        assert store.get(row.id).fix_task_id is None, why


def test_a_rejected_id_is_told_apart_from_a_missing_one(flask_client,
                                                        feature_row):
    """`{"task_id": 0}` named its field. Answering "name exactly one of
    task_id or create" sends the operator looking for a field they sent."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"task_id": 0}, headers=EDITOR)

    assert "positive whole number" in answer.get_json()["error"]


def test_a_defect_that_is_not_there_is_a_404(flask_client):
    answer = flask_client.post("/api/defects/999999/fix-item",
                               json={"create": True}, headers=EDITOR)
    assert answer.status_code == 404


def test_a_viewer_cannot_point_a_defect_at_a_fix(flask_client, feature_row):
    """Every write on this ledger is editor-level, and this one authors a row
    on a second table."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    viewer = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

    answer = flask_client.post(f"/api/defects/{row.id}/fix-item",
                               json={"create": True}, headers=viewer)

    assert answer.status_code == 403
    assert store.get(row.id).fix_task_id is None


def test_the_offer_is_raised_by_a_close_and_by_nothing_else():
    """Read off the source: the offer is client state, so a request cannot see
    it. It is the answer to "you just named a check — who writes it", which is
    a question only the operator who closed it is holding."""
    source = ACTIONS.read_text()

    assert "offered.value = which === 'close' && !props.defect.fix_task_id" \
        in source, "only a close raises it, and never over an existing pointer"
    assert "const offered = ref(false)" in source, \
        "arriving at an already-closed defect must not raise the offer"


def _create(flask_client, defect_id):
    return flask_client.post(f"/api/defects/{defect_id}/fix-item",
                             json={"create": True}, headers=EDITOR)


def _fix_task(defect_id):
    with SessionLocal() as session:
        return session.get(AgentTask, store.get(defect_id).fix_task_id)


def test_creating_twice_resolves_to_the_same_task(flask_client, feature_row):
    """A double click or a second tab must not file a second task for one bug:
    the defect id is the task's external id, so the replay finds the first."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    first = _create(flask_client, row.id).get_json()["defect"]["fix_task_id"]
    second = _create(flask_client, row.id).get_json()["defect"]["fix_task_id"]

    assert first == second
    with SessionLocal() as session:
        rows = session.exec(select(AgentTask).where(
            AgentTask.source == "defect",
            AgentTask.external_id == f"defect:{row.id}")).all()
    assert len(rows) == 1


def test_the_body_cites_the_defect_and_the_feature(flask_client,
                                                   feature_row):
    """References, not copies: the run resolves both at launch, so a symptom
    reworded or a feature renamed after the task was cut is what it reads."""
    feature = feature_row("the gutter")
    row = store.record("the gutter folds the newest span",
                       feature_id=feature.id, session_id="t-7",
                       suspect_paths=["lib/trace/merge.py"])

    _create(flask_client, row.id)

    assert _fix_task(row.id).body == (
        f"Fix regin defect [D{row.id} the gutter folds the newest span]"
        f"(defect:{row.id}), reported against feature "
        f"[F{feature.id} the gutter](feature:{feature.id}).")


def test_an_edited_surface_reaches_the_next_fix_task(flask_client,
                                                     feature_row):
    from lib.prompt_templates import (  # noqa: PLC0415
        seed_builtin_skeletons, update_template)
    from lib.prompts.surfaces.defects import (  # noqa: PLC0415
        FIX_TASK_SURFACE_ID)

    seed_builtin_skeletons()
    update_template(FIX_TASK_SURFACE_ID,
                    {"body": "REPRO FIRST #{{defect_id}}: {{symptom}}"})
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    _create(flask_client, row.id)

    assert _fix_task(row.id).body == f"REPRO FIRST #{row.id}: it broke"


def test_the_defect_screenshots_ride_the_fix_task(flask_client, feature_row):
    """A defect's images reach no model; a task's are delivered to its runs.
    Copied once, at the index the symptom's prose may already name."""
    from lib.defects import images as defect_images  # noqa: PLC0415
    from lib.tasks import images as task_images  # noqa: PLC0415

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    feature = feature_row()
    row = store.record("see ![](image:2)", feature_id=feature.id)
    defect_images.attach(row.id, png, want_idx=2)

    _create(flask_client, row.id)
    _create(flask_client, row.id)

    task_id = store.get(row.id).fix_task_id
    assert [i["idx"] for i in task_images.list_for(task_id)] == [2]
    assert task_images.get_bytes(task_id, 2)[0] == png
    assert _fix_task(row.id).body.count("![](image:2)") == 1


def test_every_screenshot_is_named_in_the_body(flask_client, feature_row):
    """The body cites the symptom instead of carrying its prose, so it has to
    name each screenshot or the run gets pictures its prompt never mentions."""
    from lib.defects import images as defect_images  # noqa: PLC0415
    from lib.tasks import images as task_images  # noqa: PLC0415

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    feature = feature_row()
    row = store.record("the badge overlaps", feature_id=feature.id,
                       session_id="t-3")
    defect_images.attach(row.id, png)
    defect_images.attach(row.id, png)

    _create(flask_client, row.id)

    body = _fix_task(row.id).body
    task_id = store.get(row.id).fix_task_id
    assert [i["idx"] for i in task_images.list_for(task_id)] == [1, 2]
    assert body.endswith(f"(feature:{feature.id}).\n![](image:1)\n![](image:2)")


def test_a_defect_without_screenshots_names_none(flask_client, feature_row):
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id, session_id="t-4")

    _create(flask_client, row.id)

    assert _fix_task(row.id).body.endswith(f"(feature:{feature.id}).")


def test_the_surface_is_listed_for_editing():
    from lib.prompts import list_surfaces  # noqa: PLC0415

    surface = next(s for s in list_surfaces() if s.id == "defect-fix-task")
    assert surface.area == "defect"
    assert {"defect_id", "defect_ref", "feature_ref", "symptom",
            "details"} <= set(surface.variable_names())


def test_create_refuses_to_override_a_pointer_it_did_not_write(
        flask_client, feature_row, native_task):
    """Re-pointing is the operator's correction; a create must not undo it."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    _create(flask_client, row.id)
    other = native_task(title="the real fix")
    store.set_fix_item(row.id, other)

    answer = _create(flask_client, row.id)

    assert answer.status_code == 409
    assert "re-point" in answer.get_json()["error"]
    assert store.get(row.id).fix_task_id == other


def test_the_fix_task_names_the_defect_it_closes(flask_client, feature_row):
    """The pointer lives on the defect; a task read from the feature page or
    its own address must still say which defect it closes, or the operator
    who just filed it cannot tell it apart from the feature's other steps."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    other = store.record("unrelated", feature_id=feature.id)
    task_id = flask_client.post(
        f"/api/defects/{row.id}/fix-item", json={"create": True},
        headers=EDITOR).get_json()["defect"]["fix_task_id"]

    listed = flask_client.get(f"/api/tasks?feature_id={feature.id}",
                              headers=EDITOR).get_json()["items"]
    detail = flask_client.get(f"/api/tasks/{task_id}",
                              headers=EDITOR).get_json()["task"]

    assert {t["id"]: t["fixes_defects"] for t in listed} == {task_id: [row.id]}
    assert detail["fixes_defects"] == [row.id]
    assert other.id not in detail["fixes_defects"]


def test_a_task_no_defect_points_at_fixes_nothing(flask_client, native_task):
    task_id = native_task()
    detail = flask_client.get(f"/api/tasks/{task_id}",
                              headers=EDITOR).get_json()["task"]
    assert detail["fixes_defects"] == []
