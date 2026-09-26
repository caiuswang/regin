"""`/api/tasks` — the JWT-authenticated browser surface.

The inverse of `/api/tasks/capture`: these routes are NOT in
PUBLIC_API_ENDPOINTS, so the app-wide JWT gate plus `require_auth` /
`require_editor` is their guard, and an API key — a different principal —
reaches none of them. That split is what keeps a machine credential out of
the SPA.
"""

from __future__ import annotations

from sqlmodel import select

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _rows():
    with SessionLocal() as session:
        return session.exec(select(AgentTask)).all()


def _create(client, headers=EDITOR, **body):
    return client.post("/api/tasks", json=body, headers=headers)


# ── creation ─────────────────────────────────────────────────

def test_editor_creates_a_web_idea_attributed_to_them(flask_client):
    resp = _create(flask_client, title="wire the tasks view", body="details",
                   tags=["ui"])

    assert resp.status_code == 201, resp.get_json()
    task = resp.get_json()["task"]
    assert task["status"] == "idea"
    assert task["source"] == "web"
    assert task["created_by"] == 2
    assert task["tags"] == ["ui"]

    rows = _rows()
    assert len(rows) == 1
    assert (rows[0].status, rows[0].source, rows[0].created_by) == (
        "idea", "web", 2)


def test_viewer_cannot_create_and_anonymous_cannot_either(flask_client,
                                                          anon_client):
    assert _create(flask_client, headers=VIEWER, title="x").status_code == 403
    assert anon_client.post("/api/tasks", json={"title": "x"}).status_code == 401
    assert _rows() == []


def test_an_api_key_is_refused_by_both_browser_routes(anon_client, mint_key):
    """A key is a different principal, not a weaker user."""
    raw, _ = mint_key()
    headers = {"Authorization": f"Bearer {raw}"}

    assert anon_client.post("/api/tasks", json={"title": "x"},
                            headers=headers).status_code == 401
    assert anon_client.get("/api/tasks", headers=headers).status_code == 401
    assert _rows() == []


def test_browser_routes_are_not_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS
    for endpoint in ("tasks.api_create_task", "tasks.api_list_tasks",
                     "tasks.api_update_task"):
        assert endpoint not in PUBLIC_API_ENDPOINTS


def test_execution_parameters_are_rejected_by_name(flask_client):
    resp = _create(flask_client, title="x", cwd="/tmp")
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "cwd"
    assert _rows() == []


def test_the_request_cannot_choose_status_or_source(flask_client):
    assert _create(flask_client, title="x", status="running").status_code == 400
    assert _create(flask_client, title="x", source="telegram").status_code == 400
    assert _rows() == []


def test_missing_and_oversized_titles_are_400(flask_client):
    assert _create(flask_client, body="no title").status_code == 400
    resp = _create(flask_client, title="t" * 201)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "title"
    assert _rows() == []


# ── listing ──────────────────────────────────────────────────

def test_list_is_newest_first_and_pages(flask_client):
    for n in range(5):
        _create(flask_client, title=f"idea {n}")

    first = flask_client.get("/api/tasks?size=2").get_json()
    assert [t["title"] for t in first["items"]] == ["idea 4", "idea 3"]
    assert first["pagination"]["has_next"] is True
    assert first["pagination"]["total"] == 5

    second = flask_client.get("/api/tasks?size=2&page=1").get_json()
    assert [t["title"] for t in second["items"]] == ["idea 2", "idea 1"]

    last = flask_client.get("/api/tasks?size=2&page=2").get_json()
    assert [t["title"] for t in last["items"]] == ["idea 0"]
    assert last["pagination"]["has_next"] is False


def test_status_filter_returns_exactly_the_archived_rows(flask_client):
    kept = _create(flask_client, title="keep").get_json()["task"]["id"]
    archived = _create(flask_client, title="drop").get_json()["task"]["id"]
    flask_client.patch(f"/api/tasks/{archived}", json={"status": "archived"},
                       headers=EDITOR)

    only_archived = flask_client.get("/api/tasks?status=archived").get_json()
    assert [t["id"] for t in only_archived["items"]] == [archived]

    only_ideas = flask_client.get("/api/tasks?status=idea").get_json()
    assert [t["id"] for t in only_ideas["items"]] == [kept]

    # Archived is a separate world: an unfiltered feed shows the live backlog,
    # and the archive is reached by asking for it — by status, or explicitly.
    unfiltered = flask_client.get("/api/tasks").get_json()
    assert [t["id"] for t in unfiltered["items"]] == [kept]
    everything = flask_client.get("/api/tasks?include_archived=1").get_json()
    assert sorted(t["id"] for t in everything["items"]) == sorted([kept, archived])


def test_list_is_empty_not_an_error_with_no_tasks(flask_client):
    payload = flask_client.get("/api/tasks").get_json()
    assert payload["items"] == []
    assert payload["pagination"]["has_next"] is False


def test_viewers_may_read_the_list(flask_client):
    _create(flask_client, title="visible")
    resp = flask_client.get("/api/tasks", headers=VIEWER)
    assert resp.status_code == 200
    assert len(resp.get_json()["items"]) == 1


# ── transitions ──────────────────────────────────────────────

def test_archive_and_unarchive_round_trip(flask_client):
    task_id = _create(flask_client, title="round trip").get_json()["task"]["id"]

    archived = flask_client.patch(f"/api/tasks/{task_id}",
                                  json={"status": "archived"}, headers=EDITOR)
    assert archived.status_code == 200
    assert archived.get_json()["task"]["status"] == "archived"

    restored = flask_client.patch(f"/api/tasks/{task_id}",
                                  json={"status": "idea"}, headers=EDITOR)
    assert restored.status_code == 200
    assert restored.get_json()["task"]["status"] == "idea"


def test_any_other_target_status_is_a_400_naming_it(flask_client):
    task_id = _create(flask_client, title="idea").get_json()["task"]["id"]

    # `done` is deliberately absent: it is the done-claim, which the edit
    # surface DOES reach — see `tests/tasks/test_done_claim.py`.
    for target in ("running", "armed", "queued", "nonsense"):
        resp = flask_client.patch(f"/api/tasks/{task_id}",
                                  json={"status": target}, headers=EDITOR)
        assert resp.status_code == 400, target
        assert target in resp.get_json()["error"]

    with SessionLocal() as session:
        assert session.get(AgentTask, task_id).status == "idea"


def test_patch_edits_text_and_tags(flask_client):
    task_id = _create(flask_client, title="before",
                      tags=["old"]).get_json()["task"]["id"]

    resp = flask_client.patch(f"/api/tasks/{task_id}",
                              json={"title": "after", "body": "more",
                                    "tags": ["new"]}, headers=EDITOR)

    task = resp.get_json()["task"]
    assert (task["title"], task["body"], task["tags"]) == ("after", "more",
                                                           ["new"])


def test_patch_cannot_rewrite_provenance_or_pass_unknown_fields(flask_client):
    task_id = _create(flask_client, title="x").get_json()["task"]["id"]

    for body in ({"source": "telegram"}, {"external_id": "tg:1"},
                 {"created_by": 9}, {"cwd": "/tmp"}):
        resp = flask_client.patch(f"/api/tasks/{task_id}", json=body,
                                  headers=EDITOR)
        assert resp.status_code == 400, body
        assert resp.get_json()["field"] in body


def test_patch_needs_an_editor_and_a_real_task(flask_client):
    task_id = _create(flask_client, title="x").get_json()["task"]["id"]

    assert flask_client.patch(f"/api/tasks/{task_id}",
                              json={"status": "archived"},
                              headers=VIEWER).status_code == 403
    assert flask_client.patch("/api/tasks/9999", json={"status": "archived"},
                              headers=EDITOR).status_code == 404


def test_anonymous_cannot_patch(anon_client):
    assert anon_client.patch("/api/tasks/1",
                             json={"status": "archived"}).status_code == 401
