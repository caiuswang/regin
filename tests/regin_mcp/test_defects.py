"""Defect tools beyond open/close: get, list by status, update, attribute,
fix-task pointers and triage — linking defects, tasks and features."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tests.regin_mcp.conftest import EDITOR


def test_linking_a_task_a_feature_and_a_defect(call):
    feature = call("feature_create", title="F")["id"]
    task = call("task_create", title="loose")["id"]
    defect = call("defect_open", symptom="orphan")["id"]
    call("task_update", task_id=task, feature_id=feature)
    call("defect_attribute", defect_id=defect, feature_id=feature)
    pointed = call("defect_set_fix_task", defect_ids=[defect], task_id=task)
    assert pointed["task_id"] == task
    shown = call("feature_get", feature_id=feature)
    assert task in [t["id"] for t in shown["tasks"]]
    assert defect in [d["id"] for d in shown["defects"]]
    assert call("defect_get", defect_id=defect)["fix_task_id"] == task
    assert defect not in [d["id"] for d in call("defect_list")["defects"]]


def test_defect_get_matches_the_web_read(call, flask_client):
    defect = call("defect_open", symptom="x", paths=["a.py"])["id"]
    web = flask_client.get(f"/api/defects/{defect}", headers=EDITOR)
    assert call("defect_get", defect_id=defect) == web.get_json()["defect"]


def test_create_and_remove_a_fix_task(call):
    feature = call("feature_create", title="F")["id"]
    a = call("defect_open", symptom="a", feature_id=feature)["id"]
    b = call("defect_open", symptom="b", feature_id=feature)["id"]
    made = call("defect_set_fix_task", defect_ids=[a, b], create=True)
    assert made["task_id"] is not None
    assert {d["fix_task_id"] for d in made["defects"]} == {made["task_id"]}
    cleared = call("defect_set_fix_task", defect_ids=[a], remove=True)
    assert cleared["defects"][0]["fix_task_id"] is None


@pytest.mark.parametrize("args,needle", [
    ({}, "exactly one"),
    ({"task_id": 1, "create": True}, "exactly one"),
    ({"remove": True, "defect_ids": []}, "at least one"),
    ({"task_id": 999}, "no task 999"),
    ({"create": True}, "unattributed"),
])
def test_fix_task_refusals(call, args, needle):
    defect = call("defect_open", symptom="d")["id"]
    with pytest.raises(ToolError, match=needle):
        call("defect_set_fix_task", **{"defect_ids": [defect], **args})


def test_update_rewrites_wording_and_paths(call):
    defect = call("defect_open", symptom="old")["id"]
    out = call("defect_update", defect_id=defect, symptom="new",
               paths=["lib/x.py"])
    assert (out["symptom"], out["suspect_paths"]) == ("new", ["lib/x.py"])
    with pytest.raises(ToolError, match="send symptom, paths, or both"):
        call("defect_update", defect_id=defect)
    with pytest.raises(ToolError, match="needs a symptom"):
        call("defect_update", defect_id=defect, symptom="  ")


def test_list_by_status(call):
    feature = call("feature_create", title="F")["id"]
    open_ = call("defect_open", symptom="open one", feature_id=feature)["id"]
    shut = call("defect_open", symptom="shut one", feature_id=feature)["id"]
    call("defect_close", defect_id=shut, check="a test")
    ids = lambda **kw: [d["id"] for d in call("defect_list",
                                              feature_id=feature,
                                              **kw)["defects"]]
    assert ids(status="open") == [open_]
    assert ids(status="closed") == [shut]
    assert ids() == [shut, open_]
    with pytest.raises(ToolError, match="unknown status"):
        call("defect_list", status="bogus")


def test_triage_decline_restore_snooze_and_merge(call):
    a = call("defect_open", symptom="first")["id"]
    b = call("defect_open", symptom="second")["id"]
    declined = call("defect_triage", defect_id=a, action="decline",
                    reason="not a bug")
    assert (declined["status"], declined["triage_reason"]) == (
        "closed", "not a bug")
    assert call("defect_triage", defect_id=a,
                action="restore")["status"] == "open"
    snoozed = call("defect_triage", defect_id=a, action="snooze")
    assert snoozed["queue_position"] != "queue"
    assert call("defect_triage", defect_id=a,
                action="unsnooze")["queue_position"] == "queue"
    kept = call("defect_triage", defect_id=b, action="merge", into_id=a)
    assert kept["id"] == a
    with pytest.raises(ToolError, match=f"no defect {b}"):
        call("defect_get", defect_id=b)
    with pytest.raises(ToolError, match="into_id"):
        call("defect_triage", defect_id=a, action="merge")


def test_reopen_needs_a_closed_defect(call):
    feature = call("feature_create", title="F")["id"]
    d = call("defect_open", symptom="s", feature_id=feature)["id"]
    with pytest.raises(ToolError):
        call("defect_triage", defect_id=d, action="reopen")
    call("defect_close", defect_id=d, check="t")
    assert call("defect_triage", defect_id=d,
                action="reopen")["status"] == "open"


@pytest.mark.parametrize("name,args", [
    ("defect_get", {"defect_id": 999}),
    ("defect_update", {"defect_id": 999, "symptom": "x"}),
    ("defect_attribute", {"defect_id": 999, "feature_id": 1}),
    ("defect_triage", {"defect_id": 999, "action": "snooze"}),
    ("defect_set_fix_task", {"defect_ids": [999], "remove": True}),
])
def test_unknown_defect_is_a_tool_error(call, name, args):
    with pytest.raises(ToolError, match="no defect 999"):
        call(name, **args)


def _fix_task(call, defect_ids):
    task = call("defect_set_fix_task", defect_ids=defect_ids[:1],
                create=True)["task_id"]
    if defect_ids[1:]:
        call("defect_set_fix_task", defect_ids=defect_ids[1:], task_id=task)
    return task


def test_linking_through_mcp_rewrites_the_task_spec(call):
    """D317 on the MCP surface: the title and the spec both name every
    defect pointed at the task, and lose one that leaves it."""
    feature = call("feature_create", title="F")["id"]
    a = call("defect_open", symptom="first", feature_id=feature)["id"]
    b = call("defect_open", symptom="second", feature_id=feature)["id"]
    task = _fix_task(call, [a, b])

    shown = call("task_get", task_id=task)
    assert shown["title"] == f"Fix #{a}, #{b}"
    assert f"](defect:{a})" in shown["body"]
    assert f"](defect:{b})" in shown["body"]

    call("defect_triage", defect_id=b, action="merge", into_id=a)

    shown = call("task_get", task_id=task)
    assert shown["title"] == "first"
    assert f"](defect:{b})" not in shown["body"]


def test_rewording_through_mcp_rewrites_the_task_spec(call):
    feature = call("feature_create", title="F")["id"]
    a = call("defect_open", symptom="old wording", feature_id=feature)["id"]
    task = _fix_task(call, [a])

    call("defect_update", defect_id=a, symptom="new wording")

    shown = call("task_get", task_id=task)
    assert shown["title"] == "new wording"
    assert f"[D{a} new wording](defect:{a})" in shown["body"]
    assert "old wording" not in shown["body"]


def test_re_attributing_through_mcp_rewrites_the_task_spec(call):
    first = call("feature_create", title="First")["id"]
    second = call("feature_create", title="Second")["id"]
    a = call("defect_open", symptom="blamed", feature_id=first)["id"]
    task = _fix_task(call, [a])

    call("defect_attribute", defect_id=a, feature_id=second)

    body = call("task_get", task_id=task)["body"]
    assert f"](feature:{second})" in body
    assert f"](feature:{first})" not in body


def test_a_hand_written_spec_survives_mcp_edits(call):
    feature = call("feature_create", title="F")["id"]
    a = call("defect_open", symptom="a", feature_id=feature)["id"]
    task = _fix_task(call, [a])
    call("task_update", task_id=task, body="my own spec")

    call("defect_update", defect_id=a, symptom="a, reworded")

    assert call("task_get", task_id=task)["body"] == "my own spec"
