"""The Tasks tab's Graph view (`/api/tasks/graph?lanes=1`) and the multi-value
priority chip (feature #110, S3)."""

from __future__ import annotations

from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskDep


def _depends(task_id: int, on: int):
    with SessionLocal() as session:
        session.add(AgentTaskDep(task_id=task_id, depends_on_id=on,
                                 kind="requires"))
        session.commit()


def _lanes(client, qs="lanes=1"):
    response = client.get(f"/api/tasks/graph?{qs}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _chain(native_task, feature_id, statuses):
    ids = [native_task(title=f"step {i}", status=s, feature_id=feature_id)
           for i, s in enumerate(statuses)]
    for child, parent in zip(ids[1:], ids):
        _depends(child, parent)
    return ids


def test_graph_draws_feature_lanes_with_their_done_counts(
        flask_client, native_task, feature_row):
    feature = feature_row("Span search facets")
    ids = _chain(native_task, feature.id, ["done", "running", "armed"])
    loose = native_task(title="loose idea")

    body = _lanes(flask_client)

    assert body["lanes"] == [
        {"feature_id": feature.id, "title": "Span search facets",
         "done": 1, "total": 3},
        {"feature_id": None, "title": "Loose tasks", "done": 0, "total": 1},
    ]
    assert sorted(n["id"] for n in body["nodes"]) == sorted([*ids, loose])
    assert {n["id"]: n["feature_id"] for n in body["nodes"]}[ids[0]] \
        == feature.id
    assert body["chains"] == 2


def test_blocking_edges_are_the_unmet_ones(flask_client, native_task,
                                           feature_row):
    feature = feature_row("chain")
    done, running, armed = _chain(native_task, feature.id,
                                  ["done", "running", "armed"])

    edges = {(e["task_id"], e["depends_on_id"]): e["blocking"]
             for e in _lanes(flask_client)["edges"]}

    assert edges == {(running, done): False, (armed, running): True}


def test_open_chains_only_hides_settled_lanes_and_counts_them(
        flask_client, native_task, feature_row):
    live = feature_row("live")
    shipped = feature_row("shipped")
    open_ids = _chain(native_task, live.id, ["done", "armed"])
    _chain(native_task, shipped.id, ["done", "done"])
    native_task(title="gone", status="archived", feature_id=live.id)

    body = _lanes(flask_client)
    everything = _lanes(flask_client, "lanes=1&open=0")

    assert sorted(n["id"] for n in body["nodes"]) == sorted(open_ids)
    assert [lane["feature_id"] for lane in body["lanes"]] == [live.id]
    assert (body["chains"], body["lanes_hidden"]) == (1, 1)
    assert [lane["feature_id"] for lane in everything["lanes"]] \
        == [live.id, shipped.id]
    assert (everything["chains"], everything["lanes_hidden"]) == (2, 0)
    assert all(n["status"] != "archived" for n in everything["nodes"])


def test_lane_graph_follows_the_project_scope(flask_client, native_task):
    mine = native_task(title="mine", project_id=7)
    native_task(title="theirs", project_id=8)
    backlog = native_task(title="backlog")

    scoped = _lanes(flask_client, "lanes=1&project_id=7")
    unscoped = _lanes(flask_client, "lanes=1&project_id=none")

    assert [n["id"] for n in scoped["nodes"]] == [mine]
    assert [n["id"] for n in unscoped["nodes"]] == [backlog]


def test_lane_graph_past_the_cap_answers_its_size(flask_client, native_task):
    for i in range(4):
        native_task(title=f"idea {i}")

    body = _lanes(flask_client, "lanes=1&cap=3")

    assert (body["truncated"], body["total_nodes"]) == (True, 4)
    assert body["nodes"] == body["lanes"] == []


def test_priority_filter_takes_a_comma_list(flask_client, native_task):
    from sqlmodel import text
    ids = {p: native_task(title=p) for p in ("urgent", "high", "low")}
    with SessionLocal() as session:
        for p, task_id in ids.items():
            session.exec(text("UPDATE agent_tasks SET priority = :p"
                              " WHERE id = :i").bindparams(p=p, i=task_id))
        session.commit()

    body = flask_client.get("/api/tasks?priority=high,urgent").get_json()
    grouped = flask_client.get(
        "/api/tasks?group=next_step&priority=urgent,high").get_json()
    refused = flask_client.get("/api/tasks?priority=high,bogus")

    assert sorted(t["id"] for t in body["items"]) \
        == sorted([ids["urgent"], ids["high"]])
    assert body["applied"]["filters"]["priority"] == "urgent,high"
    assert {g["key"]: g["n"] for g in grouped["groups"]}["ideas"] == 2
    assert refused.status_code == 400
    assert refused.get_json()["field"] == "priority"


def _set_priority(task_id: int, priority: str):
    from sqlmodel import text
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET priority = :p"
                          " WHERE id = :i").bindparams(p=priority, i=task_id))
        session.commit()


def test_lane_graph_takes_the_feeds_filters(flask_client, native_task,
                                            feature_row):
    feature = feature_row("filtered")
    tagged = native_task(title="tagged", tags_json='["release"]',
                         source="cli", assignee=3, feature_id=feature.id)
    other = native_task(title="other", source="linear")
    _set_priority(tagged, "high")
    _set_priority(other, "low")

    def ids(qs):
        return [n["id"] for n in _lanes(flask_client, f"lanes=1&{qs}")["nodes"]]

    assert ids("priority=urgent,high") == [tagged]
    assert ids("tag=release") == [tagged]
    assert ids("source=linear") == [other]
    assert ids(f"feature_id={feature.id}") == [tagged]
    assert ids("assignee=3") == [tagged]
    assert ids("root_only=1") == [other]
    refused = flask_client.get("/api/tasks/graph?lanes=1&priority=bogus")
    assert refused.status_code == 400
    assert refused.get_json()["field"] == "priority"


def test_filtered_graph_counts_what_it_draws_and_keeps_blocking_real(
        flask_client, native_task, feature_row):
    feature = feature_row("chain")
    running, armed = _chain(native_task, feature.id, ["running", "armed"])
    _set_priority(armed, "high")

    body = _lanes(flask_client, "lanes=1&priority=high")

    assert [n["id"] for n in body["nodes"]] == [armed]
    assert body["nodes"][0]["blocked"]
    assert body["edges"] == []
    assert body["lanes"] == [{"feature_id": feature.id, "title": "chain",
                              "done": 0, "total": 1}]
    assert body["chains"] == 1


def test_filtered_graph_never_draws_archived(flask_client, native_task):
    native_task(title="gone", status="archived", source="cli")
    live = native_task(title="live", source="cli")

    body = _lanes(flask_client,
                  "lanes=1&open=0&source=cli&status=archived&include_archived=1")

    assert [n["id"] for n in body["nodes"]] == [live]


def test_a_running_node_carries_its_open_run_for_watch(
        flask_client, native_task, task_run_row):
    started = native_task(title="started", status="running")
    launching = native_task(title="launching", status="running")
    task_run_row(started, trace_id="sdk-abc", status="running", ended_at=None)
    task_run_row(launching, status="running", ended_at=None)
    idle = native_task(title="idle", status="armed")

    live = {n["id"]: n["live"] for n in _lanes(flask_client)["nodes"]}

    assert live[started]["trace_id"] == "sdk-abc"
    assert live[launching]["trace_id"] is None
    assert live[idle] is None
