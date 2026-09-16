"""Who may be a parent — the candidate read behind the browser's picker.

The picker must offer exactly the set the server would accept, so the list is
computed by `grain.refuse_bad_parentage` itself rather than by a rule spelled a
second time in the client. Every exclusion below is that function's own
refusal, read through the route the SPA calls.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.tasks import grain, query, store

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _candidates(client, **args) -> list[int]:
    qs = "&".join(f"{k}={v}" for k, v in args.items())
    resp = client.get(f"/api/tasks/grain-candidates?{qs}", headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    return [row["id"] for row in resp.get_json()["items"]]


def _feed_ids(client) -> list[int]:
    resp = client.get("/api/tasks", headers=EDITOR)
    assert resp.status_code == 200
    return [row["id"] for row in resp.get_json()["items"]]


# ── the exclusions, each one a refusal ───────────────────────

def test_the_candidate_list_excludes_the_task_itself(flask_client,
                                                     native_task):
    task = native_task(title="the one being edited")
    other = native_task(title="a legal parent")

    offered = _candidates(flask_client, child=task)
    assert other in offered
    assert task not in offered


def test_the_candidate_list_excludes_a_task_that_holds_runs(
        flask_client, native_task, task_run_row):
    holder = native_task(title="a task that has been armed")
    task_run_row(holder, trace_id="t-1")
    free = native_task(title="a task that never ran")
    child = native_task(title="the child")

    offered = _candidates(flask_client, child=child)
    assert free in offered
    assert holder not in offered


def test_the_candidate_list_excludes_a_task_that_already_has_a_parent(
        flask_client, native_task):
    feature = native_task(title="a feature")
    run = native_task(title="already a run")
    grain.set_parent(run, feature)
    child = native_task(title="the child")

    offered = _candidates(flask_client, child=child)
    assert run not in offered   # the grain is exactly two deep
    assert feature in offered   # a feature may still take another run


def test_the_candidate_list_is_computed_by_the_server_not_the_client(
        flask_client, native_task, task_run_row):
    """Every row the feed shows, but only the ones the grain would accept.

    The proof is the difference: the plain feed hands the client rows the
    picker must not offer, so a client that filtered nothing would offer them.
    """
    child = native_task(title="the child")
    holder = native_task(title="holds runs")
    task_run_row(holder, trace_id="t-2")
    legal = native_task(title="legal")

    offered = _candidates(flask_client, child=child)
    assert set(offered) < set(_feed_ids(flask_client))
    assert offered == [legal]

    # And the server's own refusal agrees with what it left out.
    from lib.orm import SessionLocal

    with SessionLocal() as session:
        for task_id in offered:
            grain.refuse_bad_parentage(session, child, task_id)


def test_the_candidate_list_reads_the_other_side_for_a_runs_card(
        flask_client, native_task, task_run_row):
    """`parent=<id>` asks who may become a run OF this task, same rule."""
    feature = native_task(title="the feature standing open")
    free = native_task(title="a task that could be a run")
    holder = native_task(title="a task that holds runs")
    task_run_row(holder, trace_id="t-3")

    offered = _candidates(flask_client, parent=feature)
    assert free in offered
    assert feature not in offered
    # `holder` having runs does not stop it BEING one — only from being a
    # parent — so the rule that refuses is the only thing consulted here.
    assert holder in offered


def test_a_candidate_read_names_the_side_it_was_not_given(flask_client):
    resp = flask_client.get("/api/tasks/grain-candidates", headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] in ("child", "parent")


def test_the_default_task_feed_is_still_unchanged(flask_client, native_task):
    """The candidate read is its own route; the backlog keeps showing runs."""
    feature = native_task(title="a feature")
    run = native_task(title="a run")
    grain.set_parent(run, feature)

    assert set(_feed_ids(flask_client)) == {feature, run}
    filters = query.parse_filters({})
    assert filters["root_only"] is False and filters["parent_id"] is None


# ── the acts the picker performs ─────────────────────────────

def test_clearing_the_parent_detaches_the_run(flask_client, native_task):
    feature = native_task(title="a feature")
    run = native_task(title="a run")
    grain.set_parent(run, feature)

    resp = flask_client.patch(f"/api/tasks/{run}", json={"parent_id": None},
                              headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["task"]["parent_id"] is None
    assert store.get_task(run)["parent_id"] is None


def test_a_refused_parentage_answers_with_the_servers_own_sentence(
        flask_client, native_task, task_run_row):
    holder = native_task(title="holds runs")
    task_run_row(holder, trace_id="t-4")
    child = native_task(title="the child")

    resp = flask_client.patch(f"/api/tasks/{child}",
                              json={"parent_id": holder}, headers=EDITOR)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["field"] == "parent_id"
    assert "holds runs of its own" in body["error"]
