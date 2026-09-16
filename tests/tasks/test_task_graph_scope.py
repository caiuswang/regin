"""`GET /api/tasks/graph` — the DAG as a neighbourhood, not a corpus.

The two claims here are that a focused walk goes in BOTH directions (what a
task waits for AND what waits on it — an ancestors-only walk answers half of
one question), and that an unfocused graph past the node cap returns its size
rather than a drawing nobody can read.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.tasks import deps

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _task(client, title):
    return client.post("/api/tasks", json={"title": title},
                       headers=EDITOR).get_json()["task"]["id"]


def _chain(client, length):
    """`length` tasks where each waits for the one before it."""
    ids = [_task(client, f"n{n}") for n in range(length)]
    for child, parent in zip(ids[1:], ids):
        client.put(f"/api/tasks/{child}/deps", json={"depends_on": [parent]},
                   headers=EDITOR)
    return ids


def _nodes(client, qs=""):
    payload = client.get(f"/api/tasks/graph{qs}", headers=EDITOR).get_json()
    return sorted(n["id"] for n in payload["nodes"])


def test_an_unfocused_graph_returns_everything(flask_client):
    ids = _chain(flask_client, 4)
    assert _nodes(flask_client) == sorted(ids)


def test_a_root_walks_both_directions(flask_client):
    ids = _chain(flask_client, 5)
    middle = ids[2]

    # depth 1 from the middle reaches the parent above AND the child below.
    assert _nodes(flask_client, f"?root={middle}&depth=1") == sorted(ids[1:4])


def test_depth_bounds_the_walk(flask_client):
    ids = _chain(flask_client, 5)
    assert _nodes(flask_client, f"?root={ids[0]}&depth=1") == sorted(ids[:2])
    assert _nodes(flask_client, f"?root={ids[0]}&depth=2") == sorted(ids[:3])


def test_an_absent_depth_defaults_rather_than_returning_one_node(flask_client):
    ids = _chain(flask_client, 5)
    assert _nodes(flask_client, f"?root={ids[0]}") == sorted(
        ids[:deps.DEFAULT_DEPTH + 1])


def test_a_status_filter_never_hides_the_root_it_focuses(flask_client):
    ids = _chain(flask_client, 3)
    flask_client.post(f"/api/tasks/{ids[1]}/arm", json={"cwd": "."},
                      headers=EDITOR)

    # A focus view that can hide the task it is focused on answers a different
    # question than the one it was asked.
    scoped = _nodes(flask_client, f"?root={ids[0]}&status=armed")
    assert ids[0] in scoped and ids[1] in scoped and ids[2] not in scoped


def test_a_non_numeric_root_is_refused_not_ignored(flask_client):
    resp = flask_client.get("/api/tasks/graph?root=abc", headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "root"


def test_past_the_cap_an_unfocused_graph_reports_its_size_instead(flask_client):
    ids = _chain(flask_client, 5)

    payload = flask_client.get("/api/tasks/graph?cap=3",
                               headers=EDITOR).get_json()
    assert payload["truncated"] is True
    assert payload["nodes"] == [] and payload["edges"] == []
    assert payload["total_nodes"] == 5

    # Focusing is the way out, and it is never truncated.
    focused = flask_client.get(f"/api/tasks/graph?cap=3&root={ids[0]}&depth=1",
                               headers=EDITOR).get_json()
    assert focused["truncated"] is False
    assert len(focused["nodes"]) == 2


def test_a_caller_that_is_not_drawing_is_never_truncated(flask_client):
    _chain(flask_client, 5)

    # The dependency editor reads this endpoint for its CANDIDATE list. A cap
    # applied by default would silently offer no dependencies at all on exactly
    # the backlog where they matter most.
    payload = flask_client.get("/api/tasks/graph", headers=EDITOR).get_json()
    assert payload["truncated"] is False
    assert len(payload["nodes"]) == 5
    assert payload["node_cap"] is None
