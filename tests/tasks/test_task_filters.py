"""`GET /api/tasks` filtering, sorting, counts, and the bulk edit.

The claim under test throughout is that the *server* answers the filter. A
control whose result is computed over one loaded page answers a different
question than it appears to ask, and the counts beside it would then describe
the page instead of the backlog — so every assertion here checks the response
of a request, never a slice of one.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.tasks import runs

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _task(client, **body):
    body.setdefault("title", "a task")
    return client.post("/api/tasks", json=body, headers=EDITOR).get_json()["task"]


def _ids(client, qs=""):
    payload = client.get(f"/api/tasks{qs}", headers=EDITOR).get_json()
    return [t["id"] for t in payload["items"]]


def _project(client, name="regin"):
    """Register a repo. A task's project IS a repo — `project_id` means the
    same thing here as in `pattern_deployments`."""
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    with SessionLocal() as session:
        row = Repo(name=name, path=f"/tmp/{name}", is_active=1,
                   default_branch="main")
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


# ── text search ──────────────────────────────────────────────

def test_q_matches_title_and_body(flask_client):
    by_title = _task(flask_client, title="rewrite the graph view")
    by_body = _task(flask_client, title="unrelated", body="the graph is slow")
    _task(flask_client, title="nothing to see")

    assert sorted(_ids(flask_client, "?q=graph")) == sorted([by_title["id"],
                                                             by_body["id"]])


def test_q_treats_a_wildcard_as_a_literal(flask_client):
    literal = _task(flask_client, title="100% done")
    _task(flask_client, title="anything at all")

    # An unescaped `%` would match every row and the filter would look broken
    # only once someone typed one.
    assert _ids(flask_client, "?q=100%25") == [literal["id"]]


# ── tags ─────────────────────────────────────────────────────

def test_tag_matches_whole_tags_not_prefixes(flask_client):
    exact = _task(flask_client, title="exact", tags=["ui"])
    _task(flask_client, title="prefix", tags=["ui-polish"])

    assert _ids(flask_client, "?tag=ui") == [exact["id"]]


# ── project ──────────────────────────────────────────────────

def test_project_id_filters_and_none_means_the_backlog(flask_client):
    pid = _project(flask_client)
    filed = _task(flask_client, title="filed", project_id=pid)
    loose = _task(flask_client, title="loose")

    assert _ids(flask_client, f"?project_id={pid}") == [filed["id"]]
    assert _ids(flask_client, "?project_id=none") == [loose["id"]]


def test_a_non_numeric_project_id_is_refused_not_ignored(flask_client):
    resp = flask_client.get("/api/tasks?project_id=abc", headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


# ── priority ─────────────────────────────────────────────────

def test_priority_filters_and_sorts_by_rank_not_alphabet(flask_client):
    low = _task(flask_client, title="low", priority="low")
    urgent = _task(flask_client, title="urgent", priority="urgent")
    medium = _task(flask_client, title="medium", priority="medium")

    assert _ids(flask_client, "?priority=urgent") == [urgent["id"]]
    # Alphabetically this would be high < low < medium < none < urgent.
    assert _ids(flask_client, "?sort=priority") == [urgent["id"], medium["id"],
                                                    low["id"]]


def test_an_unknown_priority_is_refused(flask_client):
    resp = flask_client.get("/api/tasks?priority=critical", headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "priority"


def test_an_unknown_sort_falls_back_and_is_echoed(flask_client):
    resp = flask_client.get("/api/tasks?sort=whatever", headers=EDITOR)
    assert resp.status_code == 200
    assert resp.get_json()["applied"]["sort"] == "created:desc"


def test_the_retired_single_token_sort_falls_back(flask_client):
    body = flask_client.get("/api/tasks?sort=created_asc",
                            headers=EDITOR).get_json()
    assert body["applied"]["sort"] == "created:desc"


def test_priority_sort_pages_by_offset(flask_client):
    order = ['urgent', 'high', 'medium', 'low', 'none']
    for name in order:
        _task(flask_client, title=name, priority=name)

    first = flask_client.get("/api/tasks?sort=priority:asc&size=2",
                             headers=EDITOR).get_json()
    assert [t["priority"] for t in first["items"]] == order[:2]

    second = flask_client.get("/api/tasks?sort=priority:asc&size=2&page=1",
                              headers=EDITOR)
    assert second.status_code == 200, second.get_json()
    assert [t["priority"] for t in second.get_json()["items"]] == order[2:4]


def test_created_asc_reverses_the_default(flask_client):
    first = _task(flask_client, title="first")
    second = _task(flask_client, title="second")

    assert _ids(flask_client, "?sort=created:asc") == [first["id"], second["id"]]
    assert _ids(flask_client, "?sort=created:desc") == [second["id"], first["id"]]


# ── filters compose ──────────────────────────────────────────

def test_filters_are_conjunctive(flask_client):
    pid = _project(flask_client)
    wanted = _task(flask_client, title="wire the list", project_id=pid,
                   priority="high", tags=["ui"])
    _task(flask_client, title="wire the list", project_id=pid, priority="low",
          tags=["ui"])
    _task(flask_client, title="something else", project_id=pid,
          priority="high", tags=["ui"])

    assert _ids(flask_client,
                f"?q=wire&project_id={pid}&priority=high&tag=ui") == [wanted["id"]]


# ── counts ───────────────────────────────────────────────────

def test_counts_ride_every_page(flask_client):
    for n in range(3):
        _task(flask_client, title=f"idea {n}")

    first = flask_client.get("/api/tasks?size=2", headers=EDITOR).get_json()
    assert first["extras"]["counts"]["total"] == 3
    assert first["extras"]["counts"]["by_status"]["idea"] == 3

    nxt = flask_client.get("/api/tasks?size=2&page=1",
                           headers=EDITOR).get_json()
    assert nxt["extras"]["counts"]["total"] == 3


def test_population_is_the_unnarrowed_scope(flask_client):
    pid = _project(flask_client)
    _task(flask_client, title="match", project_id=pid)
    _task(flask_client, title="other", project_id=pid)
    _task(flask_client, title="elsewhere")

    body = flask_client.get(f"/api/tasks?project_id={pid}&q=match",
                            headers=EDITOR).get_json()
    assert body["pagination"]["total"] == 1
    assert body["population"] == 2
    assert body["applied"]["filters"]["q"] == "match"


def test_counts_are_status_blind_but_honour_every_other_filter(flask_client):
    pid = _project(flask_client)
    armed = _task(flask_client, title="match", project_id=pid)
    _task(flask_client, title="match", project_id=pid)
    _task(flask_client, title="elsewhere")
    flask_client.post(f"/api/tasks/{armed['id']}/arm",
                      json={"cwd": "."},
                      headers=EDITOR)

    payload = flask_client.get(f"/api/tasks/counts?project_id={pid}&status=idea",
                               headers=EDITOR).get_json()
    # Asking for `idea` must not make the `armed` chip read zero — each chip
    # reports how many it WOULD show, under the other filters.
    assert payload["by_status"]["idea"] == 1
    assert payload["by_status"]["armed"] == 1
    assert payload["total"] == 2


def test_the_archived_chip_matches_what_asking_for_archived_returns(flask_client):
    _task(flask_client, title="live")
    gone = _task(flask_client, title="gone")
    flask_client.patch(f"/api/tasks/{gone['id']}", json={"status": "archived"},
                       headers=EDITOR)

    counted = flask_client.get("/api/tasks/counts",
                               headers=EDITOR).get_json()["by_status"]["archived"]
    listed = len(_ids(flask_client, "?status=archived"))

    # The status axis owns BOTH status constraints. Leaving the archived
    # exclusion in place makes the chip read 0 over a group that has rows —
    # and a group hidden for reading 0 is unreachable.
    assert counted == listed == 1


def test_counts_exclude_archived_unless_asked_for(flask_client):
    live = _task(flask_client, title="live")
    gone = _task(flask_client, title="gone")
    flask_client.patch(f"/api/tasks/{gone['id']}", json={"status": "archived"},
                       headers=EDITOR)

    assert flask_client.get("/api/tasks/counts",
                            headers=EDITOR).get_json()["total"] == 1
    assert flask_client.get("/api/tasks/counts?include_archived=1",
                            headers=EDITOR).get_json()["total"] == 2
    assert _ids(flask_client) == [live["id"]]


# ── bulk ─────────────────────────────────────────────────────

def test_bulk_archives_every_row_it_can_and_names_the_rest(flask_client,
                                                          monkeypatch):
    a = _task(flask_client, title="a")
    b = _task(flask_client, title="b")
    live = _task(flask_client, title="live")
    monkeypatch.setattr(runs, "live_run",
                        lambda task_id: {"id": 1} if task_id == live["id"]
                        else None)

    resp = flask_client.post("/api/tasks/bulk",
                             json={"ids": [a["id"], b["id"], live["id"]],
                                   "status": "archived"}, headers=EDITOR)

    payload = resp.get_json()
    # A selection of two ideas and one task with a live agent behind it should
    # archive the two, not fail whole because one cannot move.
    assert sorted(payload["changed"]) == sorted([a["id"], b["id"]])
    assert [r["id"] for r in payload["rejected"]] == [live["id"]]


def test_bulk_files_tasks_under_a_project(flask_client):
    pid = _project(flask_client)
    a = _task(flask_client, title="a")
    b = _task(flask_client, title="b")

    flask_client.post("/api/tasks/bulk",
                      json={"ids": [a["id"], b["id"]], "project_id": pid},
                      headers=EDITOR)

    assert _ids(flask_client, f"?project_id={pid}") == [b["id"], a["id"]]


def test_bulk_refuses_a_field_it_does_not_own(flask_client):
    a = _task(flask_client, title="a")
    resp = flask_client.post("/api/tasks/bulk",
                             json={"ids": [a["id"]], "title": "renamed"},
                             headers=EDITOR)
    # Nothing about selecting fifty rows makes overwriting fifty titles sane —
    # and the refusal must name the field it is actually about, or a client
    # that highlights `field` points at the wrong control.
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "title"


def test_bulk_refuses_a_malformed_id_list(flask_client):
    assert flask_client.post("/api/tasks/bulk",
                             json={"ids": "1,2", "status": "archived"},
                             headers=EDITOR).status_code == 400


def test_bulk_reports_an_unknown_task_rather_than_failing(flask_client):
    a = _task(flask_client, title="a")
    payload = flask_client.post("/api/tasks/bulk",
                                json={"ids": [a["id"], 9999],
                                      "status": "archived"},
                                headers=EDITOR).get_json()
    assert payload["changed"] == [a["id"]]
    assert payload["rejected"][0]["id"] == 9999


# ── grouping counts ──────────────────────────────────────────

def test_every_group_axis_is_blind_to_its_own_filter(flask_client):
    pid = _project(flask_client)
    _task(flask_client, title="a", project_id=pid, priority="high")
    _task(flask_client, title="b", project_id=pid, priority="low")
    _task(flask_client, title="c", priority="high")

    payload = flask_client.get(
        f"/api/tasks/counts?priority=high&project_id={pid}",
        headers=EDITOR).get_json()

    # Each header reports how many it WOULD show if you clicked it — so the
    # priority axis ignores the priority filter (but still honours project),
    # and the project axis ignores the project filter (but still honours
    # priority). Counting an axis inside its own filter makes every header
    # read the size of its own slice.
    assert payload["by_priority"] == {"high": 1, "low": 1}
    assert payload["by_project"] == {str(pid): 1, "none": 1}


def test_group_counts_still_honour_the_other_filters(flask_client):
    pid = _project(flask_client)
    _task(flask_client, title="match", project_id=pid, priority="high")
    _task(flask_client, title="elsewhere", priority="high")

    payload = flask_client.get(f"/api/tasks/counts?project_id={pid}",
                               headers=EDITOR).get_json()
    assert payload["by_priority"] == {"high": 1}


# ── a project is a repo ──────────────────────────────────────

def test_a_task_is_filed_under_a_registered_repo_or_nothing(flask_client):
    pid = _project(flask_client, name="some-repo")

    filed = _task(flask_client, title="filed", project_id=pid)
    assert filed["project_id"] == pid

    # There is no foreign key, exactly as `pattern_deployments.project_id` has
    # none — so the check is the writer's, and an unregistered id is refused
    # rather than stored as a chip nothing can resolve.
    refused = flask_client.post("/api/tasks",
                                json={"title": "nowhere", "project_id": 9999},
                                headers=EDITOR)
    assert refused.status_code == 400
    assert refused.get_json()["field"] == "project_id"


def test_a_dropped_repo_leaves_its_tasks_in_the_backlog(flask_client):
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    pid = _project(flask_client, name="doomed")
    tid = _task(flask_client, title="orphan", project_id=pid)["id"]

    with SessionLocal() as session:
        session.delete(session.get(Repo, pid))
        session.commit()

    # The task survives its repo. It reads as unfiled to anything that resolves
    # the id, and the backlog filter is what finds it again.
    task = flask_client.get(f"/api/tasks/{tid}", headers=EDITOR).get_json()["task"]
    assert task["id"] == tid
    assert task["project_id"] == pid
    listed = flask_client.get(f"/api/tasks?project_id={pid}",
                              headers=EDITOR).get_json()["items"]
    # The detail read carries the same row facts the list does, so a verb
    # offered on the list row is the verb offered on the task's page.
    assert listed == [task]


# ── every surface that can set a project ─────────────────────

def test_a_project_is_settable_at_capture_on_edit_and_in_bulk(flask_client):
    first = _project(flask_client, name="repo-one")
    second = _project(flask_client, name="repo-two")

    # 1. at capture
    task = _task(flask_client, title="filed at birth", project_id=first)
    assert task["project_id"] == first

    # 2. on a single existing task — the edit sheet's PATCH
    moved = flask_client.patch(f"/api/tasks/{task['id']}",
                               json={"project_id": second},
                               headers=EDITOR).get_json()["task"]
    assert moved["project_id"] == second

    # 3. across a selection
    flask_client.post("/api/tasks/bulk",
                      json={"ids": [task["id"]], "project_id": first},
                      headers=EDITOR)
    assert _ids(flask_client, f"?project_id={first}") == [task["id"]]

    # …and back to the backlog from the same edit surface.
    unfiled = flask_client.patch(f"/api/tasks/{task['id']}",
                                 json={"project_id": None},
                                 headers=EDITOR).get_json()["task"]
    assert unfiled["project_id"] is None


def test_an_edit_that_does_not_mention_the_project_leaves_it_alone(flask_client):
    pid = _project(flask_client, name="untouched")
    task = _task(flask_client, title="keep my filing", project_id=pid)

    edited = flask_client.patch(f"/api/tasks/{task['id']}",
                                json={"title": "renamed"},
                                headers=EDITOR).get_json()["task"]
    assert edited["project_id"] == pid


# ── the axes the toolbar can now reach ───────────────────────

def test_assignee_narrows_to_one_persons_claims(flask_client, native_task):
    mine = native_task(title="claimed", assignee=2)
    native_task(title="someone else's", assignee=3)
    native_task(title="nobody's")

    assert _ids(flask_client, "?assignee=2") == [mine]


def test_an_unknown_assignee_is_refused_not_dropped(flask_client, native_task):
    native_task(title="claimed", assignee=2)

    # Dropped, this would answer with the whole backlog under a control that
    # says one person's name.
    assert flask_client.get("/api/tasks?assignee=nobody",
                            headers=EDITOR).status_code == 400


def test_tag_and_source_narrow_the_feed(flask_client, native_task):
    tagged = native_task(title="tagged", tags_json='["release"]', source="cli")
    native_task(title="other tag", tags_json='["ui"]', source="cli")
    native_task(title="other source", tags_json='["release"]', source="linear")

    assert _ids(flask_client, "?tag=release&source=cli") == [tagged]
