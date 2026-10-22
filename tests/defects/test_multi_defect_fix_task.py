"""Several defects, one fix task (feature #143, A-R1..A-R8).

A task that fixes several defects is told about all of them at launch, is
created in one act for a selection, and keeps a title naming them while regin
still owns it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import func, select

from lib.auth import create_token
from lib.defects import fix_items, store
from lib.defects import images as defect_images
from lib.orm import SessionLocal
from lib.orm.models.tasks import TASK_KIND_FIX, AgentTask
from lib.tasks import images as task_images
from lib.tasks import runs
from lib.tasks.store import capture_task, update_task

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32


def _task(task_id: int) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _task_count() -> int:
    with SessionLocal() as session:
        return session.exec(select(func.count()).select_from(AgentTask)).one()


def _defects(feature_id: int, *symptoms: str):
    return [store.record(s, feature_id=feature_id) for s in symptoms]


@pytest.fixture
def launched(flask_client, monkeypatch):
    """Arm and run a task through the route; the prompt it launched with."""
    prompts = []

    def _fake(prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(trace_id=f"sdk-fake{len(prompts)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)

    def _run(task_id: int) -> str:
        assert flask_client.post(f"/api/tasks/{task_id}/arm",
                                 json={}).status_code == 200
        resp = flask_client.post(f"/api/tasks/{task_id}/run")
        assert resp.status_code == 201, resp.get_json()
        return prompts[-1]
    return _run


def _reference_lines(prompt: str) -> list[str]:
    assert prompt.count("<references>") == 1, prompt
    inner = prompt.split("<references>\n", 1)[1].split("\n</references>")[0]
    return [line for line in inner.split("\n") if line.startswith("- ")]


def test_every_linked_defect_reaches_the_run_s_references(launched,
                                                          feature_row):
    feature = feature_row()
    view, _ = capture_task(title="修复所有关联defects", source="web",
                           body="修复所有关联defects")
    low, high = _defects(feature.id, "the open symptom", "the closed symptom")
    store.set_fix_item(high.id, view["id"])
    store.set_fix_item(low.id, view["id"])
    store.close(high.id, missing_check="test_closed")

    lines = _reference_lines(launched(view["id"]))

    assert [line for line in lines if "— defect," in line] == [
        f"- D{low.id} 「the open symptom」 — defect, status open, "
        f"feature #{feature.id}",
        f"- D{high.id} 「the closed symptom」 — defect, status closed, "
        f"feature #{feature.id} — missing check: test_closed",
    ], lines

def test_a_defect_the_body_already_cites_is_listed_once(launched, feature_row):
    feature = feature_row()
    row = store.record("cited and linked", feature_id=feature.id)
    task = fix_items.create_for(row.id).fix_task_id

    lines = _reference_lines(launched(task))

    assert sum(f"D{row.id} " in line for line in lines) == 1, lines


def test_one_task_from_three_defects(flask_client, feature_row):
    feature = feature_row()
    d10, d11, d12 = _defects(feature.id, "ten broke " + "x" * 300,
                             "eleven broke", "twelve broke")
    before = _task_count()

    resp = flask_client.post(
        "/api/defects/fix-task",
        json={"defect_ids": [d12.id, d10.id, d11.id]}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert _task_count() == before + 1
    task = _task(task_id)
    assert (task.kind, task.source, task.feature_id) == (
        TASK_KIND_FIX, "defect", feature.id)
    assert task.external_id == f"defect:{d10.id}"
    assert {store.get(d.id).fix_task_id for d in (d10, d11, d12)} == {task_id}
    assert task.title == f"Fix #{d10.id}, #{d11.id}, #{d12.id}"
    assert task.auto_title == task.title
    assert [d["id"] for d in resp.get_json()["defects"]] == [
        d10.id, d11.id, d12.id]


def test_resubmitting_a_batch_returns_the_same_task(flask_client,
                                                    feature_row):
    feature = feature_row()
    d10, d11, d12 = _defects(feature.id, "ten", "eleven", "twelve")
    first = flask_client.post(
        "/api/defects/fix-task",
        json={"defect_ids": [d12.id, d10.id, d11.id]}, headers=EDITOR)
    task_id = first.get_json()["task_id"]
    before = _task_count()

    again = flask_client.post(
        "/api/defects/fix-task",
        json={"defect_ids": [d10.id, d11.id, d12.id]}, headers=EDITOR)

    assert again.status_code == 200, again.get_json()
    assert again.get_json()["task_id"] == task_id
    assert _task_count() == before


def test_batch_is_all_or_none(flask_client, feature_row, native_task):
    feature = feature_row()
    d10, d13 = _defects(feature.id, "ten", "thirteen")
    elsewhere = native_task(title="task 99")
    store.set_fix_item(d13.id, elsewhere)
    before = _task_count()

    resp = flask_client.post("/api/defects/fix-task",
                             json={"defect_ids": [d10.id, d13.id]},
                             headers=EDITOR)

    assert resp.status_code == 409
    assert resp.get_json()["blocking"] == [d13.id]
    assert store.get(d10.id).fix_task_id is None
    assert _task_count() == before


def test_batch_names_every_blocking_defect(flask_client, feature_row):
    """Unattributed and cross-feature rows are refused together."""
    one, other = feature_row("one"), feature_row("other")
    ok = store.record("ok", feature_id=one.id)
    stray = store.record("stray", feature_id=other.id)
    loose = store.record("loose", session_id="t-1")

    resp = flask_client.post(
        "/api/defects/fix-task",
        json={"defect_ids": [ok.id, stray.id, loose.id]}, headers=EDITOR)

    assert resp.status_code == 409
    assert sorted(resp.get_json()["blocking"]) == sorted([stray.id, loose.id])


@pytest.mark.parametrize("body,code", [
    ({}, 400), ({"defect_ids": []}, 400), ({"defect_ids": [True]}, 400),
    ({"defect_ids": [987654]}, 404),
])
def test_a_malformed_batch_is_refused(flask_client, body, code):
    resp = flask_client.post("/api/defects/fix-task", json=body,
                             headers=EDITOR)
    assert resp.status_code == code


def test_a_replay_refuses_when_the_lowest_defect_was_re_pointed(
        feature_row, native_task):
    """Keyed on the lowest id, but a replay must not pull that defect back
    from the task an operator moved it to."""
    feature = feature_row()
    d10, d11 = _defects(feature.id, "ten", "eleven")
    fix_items.create_batch([d10.id, d11.id])
    moved = native_task(title="hand-made")
    store.set_fix_item(d10.id, moved)

    with pytest.raises(fix_items.BatchRefused) as refused:
        fix_items.create_batch([d10.id, d11.id])

    assert refused.value.blocking == [d10.id]
    assert store.get(d10.id).fix_task_id == moved


def test_linking_a_second_defect_retitles_an_auto_titled_task(feature_row):
    feature = feature_row()
    d10, d11, d12 = _defects(feature.id, "ten symptom", "eleven", "twelve")
    task_id = fix_items.create_for(d10.id).fix_task_id
    assert _task(task_id).title == "ten symptom"

    store.set_fix_item(d11.id, task_id)

    assert _task(task_id).title == f"Fix #{d10.id}, #{d11.id}"

    store.clear_fix_item(d11.id)

    assert _task(task_id).title == "ten symptom"

    update_task(task_id, {"title": "x"})
    store.set_fix_item(d12.id, task_id)

    assert _task(task_id).title == "x"


def test_linking_a_second_defect_rewrites_an_auto_written_body(feature_row):
    """D317: the title named both defects while the spec still cited one."""
    feature = feature_row()
    d10, d11, d12 = _defects(feature.id, "ten symptom", "eleven", "twelve")
    task_id = fix_items.create_for(d10.id).fix_task_id
    assert f"](defect:{d11.id})" not in _task(task_id).body

    store.set_fix_item(d11.id, task_id)

    body = _task(task_id).body
    assert f"](defect:{d10.id})" in body and f"](defect:{d11.id})" in body
    assert body == fix_items.task_body([store.get(d10.id), store.get(d11.id)])

    store.clear_fix_item(d11.id)

    assert f"](defect:{d11.id})" not in _task(task_id).body

    update_task(task_id, {"body": "my own spec"})
    store.set_fix_item(d12.id, task_id)

    assert _task(task_id).body == "my own spec"


def test_a_hand_made_task_body_is_never_rewritten(feature_row, native_task):
    feature = feature_row()
    d10, = _defects(feature.id, "ten")
    task_id = native_task(title="mine")
    before = _task(task_id).body

    store.set_fix_item(d10.id, task_id)

    assert _task(task_id).body == before


def test_merging_into_a_defect_on_another_task_rewrites_its_body(feature_row):
    feature = feature_row()
    d10, d11 = _defects(feature.id, "ten", "eleven")
    task_id = fix_items.create_for(d10.id).fix_task_id
    store.set_fix_item(d11.id, task_id)

    store.merge(d11.id, into=d10.id)

    body = _task(task_id).body
    assert f"](defect:{d10.id})" in body
    assert f"](defect:{d11.id})" not in body


def test_re_pointing_a_defect_retitles_the_task_it_left(feature_row):
    feature = feature_row()
    d10, d11, d12 = _defects(feature.id, "ten", "eleven", "twelve")
    first, _ = fix_items.create_batch([d10.id, d11.id])
    second = fix_items.create_for(d12.id).fix_task_id

    store.set_fix_item(d11.id, second)

    assert _task(first).title == "ten"
    assert _task(second).title == f"Fix #{d11.id}, #{d12.id}"


def test_a_hand_made_task_is_never_retitled(feature_row, native_task):
    feature = feature_row()
    d10, d11 = _defects(feature.id, "ten", "eleven")
    task_id = native_task(title="mine")

    store.set_fix_item(d10.id, task_id)
    store.set_fix_item(d11.id, task_id)

    assert _task(task_id).title == "mine"


def test_create_on_a_defect_already_on_a_defect_sourced_task(feature_row):
    feature = feature_row()
    d10, d11 = _defects(feature.id, "ten", "eleven")
    task_id = fix_items.create_for(d10.id).fix_task_id
    store.set_fix_item(d11.id, task_id)
    before = _task_count()

    row = fix_items.create_for(d11.id)

    assert row.id == d11.id and row.fix_task_id == task_id
    assert _task(task_id).external_id == f"defect:{d10.id}"
    assert _task_count() == before


@pytest.mark.parametrize("finished", ["done", "archived"])
def test_create_on_a_drift_defect_files_a_new_task(feature_row, native_task,
                                                   finished):
    feature = feature_row()
    row = store.record("ten", feature_id=feature.id)
    old = native_task(title="hand-made", feature_id=feature.id)
    store.set_fix_item(row.id, old)
    with SessionLocal() as session:
        session.get(AgentTask, old).status = finished
        session.commit()

    first = fix_items.create_for(row.id)
    again = fix_items.create_for(row.id)

    assert first.fix_task_id not in (None, old)
    assert again.fix_task_id == first.fix_task_id
    assert _task(first.fix_task_id).status == "idea"
    assert _task(old).status == finished


def test_create_still_refuses_a_task_not_created_from_a_defect(
        feature_row, native_task):
    feature = feature_row()
    row = store.record("ten", feature_id=feature.id)
    store.set_fix_item(row.id, native_task(title="hand-made"))

    with pytest.raises(store.DefectError):
        fix_items.create_for(row.id)


def test_batch_body_cites_every_defect_and_copies_no_image(feature_row):
    feature = feature_row()
    rows = _defects(feature.id, "ten", "eleven", "twelve")
    defect_images.attach(rows[1].id, GIF)

    task_id, _ = fix_items.create_batch([r.id for r in reversed(rows)])

    body = _task(task_id).body
    for row in rows:
        assert body.count(f"](defect:{row.id})") == 1, body
    assert body.index(f"defect:{rows[0].id}") \
        < body.index(f"defect:{rows[2].id}")
    assert f"](feature:{feature.id})" in body
    assert "eleven" in body and "![](image:" not in body
    assert task_images.list_for(task_id) == []


def test_a_launch_names_how_to_fetch_a_linked_defect_screenshot(feature_row):
    feature = feature_row()
    rows = _defects(feature.id, "ten", "eleven")
    defect_images.attach(rows[1].id, PNG, want_idx=3)
    task_id, _ = fix_items.create_batch([r.id for r in rows])

    from lib.tasks.run_prompt import _references_block  # noqa: PLC0415

    block = _references_block({"id": task_id, "body": _task(task_id).body},
                              None)
    line = next(ln for ln in block.splitlines() if f"D{rows[1].id}" in ln)
    assert "screenshots 3" in line
    assert f"defects image {rows[1].id}" in line


def test_a_stale_seeded_fix_task_template_heals_to_the_batch_default():
    """The first seed copied the lowest defect's symptom; an install still
    holding it unedited must render every defect, not only that one."""
    from lib.orm.models.prompts import PromptTemplate  # noqa: PLC0415
    from lib.prompt_templates import seed_builtin_skeletons  # noqa: PLC0415
    from lib.prompts.surfaces.defects import _FIX_TASK_BODY  # noqa: PLC0415

    first_seed = ("Fix regin defect #{{defect_id}}, reported against feature "
                  "{{feature_named}}:\n\n{{symptom}}\n{{details}}")
    seed_builtin_skeletons()
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == "defect-fix-task")).one()
        row.body = first_seed
        session.add(row)
        session.commit()

    seed_builtin_skeletons()

    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == "defect-fix-task")).one()
    assert row.body == _FIX_TASK_BODY


@pytest.mark.parametrize("keep", [0, 1])
def test_merging_defects_retitles_the_task_they_shared(feature_row, keep):
    feature = feature_row()
    rows = _defects(feature.id, "ten", "eleven")
    task_id, _ = fix_items.create_batch([r.id for r in rows])
    keeper, dup = rows[keep], rows[1 - keep]

    store.merge(dup.id, into=keeper.id)

    assert _task(task_id).title == keeper.symptom


def test_a_create_that_died_before_its_body_writes_it_on_retry(
        feature_row, monkeypatch):
    feature = feature_row()
    row = store.record("ten", feature_id=feature.id)
    real = fix_items.task_body

    def _dies(*args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(fix_items, "task_body", _dies)
    with pytest.raises(RuntimeError):
        fix_items.create_for(row.id)
    monkeypatch.setattr(fix_items, "task_body", real)

    task_id = fix_items.create_for(row.id).fix_task_id

    assert f"defect:{row.id}" in _task(task_id).body
