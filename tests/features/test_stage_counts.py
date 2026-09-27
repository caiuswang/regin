"""Features tab: stage counts and the attribution header (feature #110, S1)."""

from __future__ import annotations

import pytest

from lib.features import events


def _index(client, query=""):
    response = client.get(f"/api/features{query}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


@pytest.fixture
def every_stage(feature_row, defect_row, session_row, task_lane):
    feature_row("spec a")
    feature_row("spec b")
    building = feature_row("building", stage=events.BUILD_STARTED)
    feature_row("shipped", stage=events.SHIPPED)
    escaped = feature_row("escaped", stage=events.SHIPPED)
    defect_row(escaped.id)
    feature_row("abandoned", stage=events.ABANDONED)
    session_row("t-claimed", cost=3.0)
    session_row("t-loose", cost=1.0)
    task_lane(9001, building.id, [("t-claimed", "done")])


LIVE = ("spec", "building", "shipped", "escaped")


def test_features_stage_counts_and_attribution_header(flask_client,
                                                      every_stage):
    body = _index(flask_client)
    stage_counts = body["extras"]["stage_counts"]
    counts = flask_client.get("/api/pipeline/counts").get_json()["counts"]
    header = body["extras"]["attribution"]

    assert stage_counts == {"all": 5, "spec": 2, "building": 1, "shipped": 1,
                            "escaped": 1}
    assert stage_counts["all"] == sum(stage_counts[s] for s in LIVE)
    assert (counts["features"], body["population"]) == (5, 5)
    assert "abandoned" not in {row["stage"] for row in body["items"]}
    assert (header["declared_sessions"], header["all_sessions"],
            header["sessions_pct"]) == (1, 2, 50)
    assert (header["declared_usd"], header["all_usd"]) == (3.0, 4.0)


def test_stage_counts_are_stage_blind_but_follow_search(flask_client,
                                                        every_stage):
    whole = _index(flask_client)["extras"]["stage_counts"]
    narrowed = _index(flask_client, "?stage=spec")
    searched = _index(flask_client, "?q=spec")["extras"]["stage_counts"]
    abandoned = _index(flask_client, "?stage=abandoned")["items"]

    assert narrowed["extras"]["stage_counts"] == whole
    assert len(narrowed["items"]) == 2
    assert (searched["all"], searched["spec"]) == (2, 2)
    assert [r["title"] for r in abandoned] == ["abandoned"]


def test_attribution_header_over_no_sessions_has_no_percentage(flask_client):
    header = _index(flask_client)["extras"]["attribution"]

    assert header["sessions_pct"] is None
    assert header["all_sessions"] == 0


def test_feature_row_counts_its_done_steps(flask_client, feature_row,
                                           native_task):
    feature = feature_row("with steps")
    native_task(title="done", status="done", feature_id=feature.id)
    native_task(title="open", status="armed", feature_id=feature.id)
    native_task(title="gone", status="archived", feature_id=feature.id)

    row = _index(flask_client)["items"][0]

    assert (row["tasks_done"], row["tasks"]) == (1, 2)
