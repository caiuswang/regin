"""Feature tools beyond create/read: update, lifecycle, link, attribute,
steps, slices and transcribe — each over the store its route calls."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.features import FeatureEvent
from lib.orm.models.trace import Session as TraceSession

SPEC = (
    "## Acceptance\n\n```gherkin\nScenario: test_a\n  Then a\n\nScenario: test_b\n  Then b\n```\n\n## Work slices\n\n"
    "| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | "
    "Skill | Verify |\n|---|---|---|---|---|---|---|---|---|\n"
    "| S1 | contracts | the contracts | R1 | test_a | — | f9/integration | "
    "goal-verified | pytest tests/a |\n"
    "| S2 | runner | the runner | R2 | test_b | S1 | f9/integration | "
    "— | — |\n")


@pytest.fixture
def feature(call):
    return call("feature_create", title="F", spec="a spec")["id"]


def _last_event(feature_id: int) -> FeatureEvent:
    with SessionLocal() as s:
        return s.exec(select(FeatureEvent)
                      .where(FeatureEvent.feature_id == feature_id)
                      .order_by(FeatureEvent.id.desc())).first()


def test_ship_is_an_operator_act_and_a_second_ship_is_refused(call, feature):
    out = call("feature_lifecycle", feature_id=feature, action="ship",
               session_id="S")
    assert out["stage"] == "shipped"
    assert call("feature_get", feature_id=feature)["stage"] == "shipped"
    event = _last_event(feature)
    assert (event.kind, event.declared_by, event.trace_id) == (
        "shipped", "operator", "S")
    with pytest.raises(ToolError, match=f"#{feature} is already shipped"):
        call("feature_lifecycle", feature_id=feature, action="ship")


def test_reopen_and_abandon(call, feature):
    with pytest.raises(ToolError, match="nothing to reopen"):
        call("feature_lifecycle", feature_id=feature, action="reopen")
    assert call("feature_lifecycle", feature_id=feature,
                action="abandon")["stage"] == "abandoned"
    assert call("feature_lifecycle", feature_id=feature,
                action="reopen")["stage"] == "spec"


def test_transcribe_lands_idea_tasks_and_arms_nothing(call):
    feature = call("feature_create", title="sliced", spec=SPEC)["id"]
    report = call("feature_slices", feature_id=feature)
    assert [r["title"] for r in report["rows"]] == ["contracts", "runner"]
    summary = call("feature_transcribe", feature_id=feature)
    assert len(summary["created"]) == 2
    tasks = call("feature_get", feature_id=feature)["tasks"]
    assert len(tasks) == 2
    assert {t["status"] for t in tasks} == {"idea"}
    second = next(t for t in tasks if "runner" in t["title"])
    first = next(t for t in tasks if t is not second)
    assert [d["id"] for d in call("task_get",
                                  task_id=second["id"])["deps"]] == [
        first["id"]]


def test_slices_of_a_spec_without_a_table_say_so(call, feature):
    report = call("feature_slices", feature_id=feature)
    assert report.get("refusal")


def test_update_title_project_branch(call, feature, project):
    out = call("feature_update", feature_id=feature, title="renamed",
               project=project, branch="f169/integration")
    assert (out["title"], out["project"], out["branch"]) == (
        "renamed", project, "f169/integration")
    cleared = call("feature_update", feature_id=feature, project="",
                   branch="")
    assert (cleared["title"], cleared["project"], cleared["branch"]) == (
        "renamed", None, None)
    with pytest.raises(ToolError, match="branch"):
        call("feature_update", feature_id=feature, branch="bad name..")
    with pytest.raises(ToolError, match="no project named 'nope'"):
        call("feature_update", feature_id=feature, project="nope")


def test_link_and_unlink(call, feature):
    other = call("feature_create", title="G")["id"]
    links = call("feature_link", feature_id=feature, other_id=other,
                 kind="follows")["links"]
    assert other in [ref["id"] for refs in links.values() for ref in refs]
    assert call("feature_get", feature_id=feature)["links"] == links
    with pytest.raises(ToolError, match="itself|same"):
        call("feature_link", feature_id=feature, other_id=feature,
             kind="related")
    after = call("feature_link", feature_id=feature, other_id=other,
                 kind="follows", remove=True)["links"]
    assert other not in [r["id"] for refs in after.values() for r in refs]


def test_attribute_a_captured_session_and_withdraw_it(call, feature):
    with SessionLocal() as s:
        s.add(TraceSession(trace_id="trace-x",
                           started_at="2026-10-22 10:00:00",
                           last_seen="2026-10-22 10:00:00"))
        s.commit()
    out = call("feature_attribute", feature_id=feature, session_id="trace-x")
    assert (out["trace_id"], out["provenance"]) == ("trace-x", "recalled")
    gone = call("feature_attribute", feature_id=feature,
                session_id="trace-x", remove=True)
    assert gone["unattributed"] is True
    with pytest.raises(ToolError, match="has no declaration"):
        call("feature_attribute", feature_id=feature, session_id="trace-x",
             remove=True)


def test_attributing_an_uncaptured_session_is_refused(call, feature):
    with pytest.raises(ToolError, match="regin has no session ghost"):
        call("feature_attribute", feature_id=feature, session_id="ghost")


def test_open_step_is_idempotent(call, feature):
    first = call("feature_open_step", feature_id=feature, stage="build",
                 body="do it")
    again = call("feature_open_step", feature_id=feature, stage="build")
    assert (first["created"], again["created"]) == (True, False)
    assert first["task"]["id"] == again["task"]["id"]
    with pytest.raises(ToolError, match="stage must be one of"):
        call("feature_open_step", feature_id=feature, stage="nope")
    with pytest.raises(ToolError, match="body"):
        call("feature_open_step", feature_id=feature, stage="spec",
             body="x" * 100_000)


@pytest.mark.parametrize("name,args", [
    ("feature_update", {"feature_id": 999, "title": "x"}),
    ("feature_lifecycle", {"feature_id": 999, "action": "ship"}),
    ("feature_attribute", {"feature_id": 999, "session_id": "s"}),
    ("feature_slices", {"feature_id": 999}),
    ("feature_transcribe", {"feature_id": 999}),
    ("feature_open_step", {"feature_id": 999, "stage": "build"}),
])
def test_unknown_feature_is_a_tool_error(call, name, args):
    with pytest.raises(ToolError, match="no feature 999"):
        call(name, **args)
