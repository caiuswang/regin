"""Images attached to a captured idea — storage, the routes, and delivery.

The invariant these pin is what separates `agent_task_images` from
`bridge_attachments`: an image belongs to the TASK, so it is re-read by every
attempt rather than claimed and spent by one prompt, and a freed `idx` is never
reissued — a thumbnail on screen cannot quietly become a different picture.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from lib.auth import create_token
from lib.tasks import images, runs

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
GIF = b"GIF89a" + b"\x00" * 16
NOT_AN_IMAGE = b"<svg>nope</svg>"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _capture(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body},
                       headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _attach(client, task_id, data=PNG, headers=EDITOR):
    return client.post(f"/api/tasks/{task_id}/images",
                       json={"data_b64": _b64(data)}, headers=headers)


# ── attaching ────────────────────────────────────────────────

def test_attaching_numbers_images_in_order_and_lists_them_on_the_task(
        flask_client):
    task_id = _capture(flask_client)

    first = _attach(flask_client, task_id, PNG)
    second = _attach(flask_client, task_id, GIF)

    assert first.status_code == 201, first.get_json()
    assert first.get_json()["image"]["idx"] == 1
    assert first.get_json()["image"]["media_type"] == "image/png"
    assert second.get_json()["image"]["idx"] == 2
    assert second.get_json()["image"]["media_type"] == "image/gif"

    listed = flask_client.get("/api/tasks", headers=EDITOR).get_json()["items"]
    assert [img["idx"] for img in listed[0]["images"]] == [1, 2]
    # The bytes are never inlined into a list read.
    assert "data_b64" not in listed[0]["images"][0]


def test_the_media_type_comes_from_the_bytes_not_the_caller(flask_client):
    """A paste event's claimed type is not evidence — the magic bytes are."""
    task_id = _capture(flask_client)

    resp = _attach(flask_client, task_id, NOT_AN_IMAGE)

    assert resp.status_code == 400
    assert "not a supported image" in resp.get_json()["error"]
    assert images.list_for(task_id) == []


def test_a_malformed_body_is_a_400_naming_the_field(flask_client):
    task_id = _capture(flask_client)

    assert _attach(flask_client, task_id).status_code == 201
    missing = flask_client.post(f"/api/tasks/{task_id}/images", json={},
                                headers=EDITOR)
    garbage = flask_client.post(f"/api/tasks/{task_id}/images",
                                json={"data_b64": "not base64!!"},
                                headers=EDITOR)

    assert missing.status_code == 400
    assert missing.get_json()["field"] == "data_b64"
    assert garbage.status_code == 400
    assert "valid base64" in garbage.get_json()["error"]


def test_an_oversized_image_is_refused_with_the_ceiling(flask_client,
                                                        monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings, "prompt_image_max_bytes", 16)
    task_id = _capture(flask_client)

    resp = _attach(flask_client, task_id, PNG)

    assert resp.status_code == 400
    assert "larger than 16 bytes" in resp.get_json()["error"]


def test_the_per_task_count_ceiling_is_enforced(flask_client, monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings, "prompt_images_max_count", 2)
    task_id = _capture(flask_client)

    assert _attach(flask_client, task_id).status_code == 201
    assert _attach(flask_client, task_id).status_code == 201
    third = _attach(flask_client, task_id)

    assert third.status_code == 400
    assert "at most 2 images per task" in third.get_json()["error"]


def test_attaching_to_an_unknown_task_is_a_404(flask_client):
    assert _attach(flask_client, 999).status_code == 404


# ── reading and removing ─────────────────────────────────────

def test_the_bytes_come_back_with_their_own_media_type(flask_client):
    task_id = _capture(flask_client)
    _attach(flask_client, task_id, GIF)

    resp = flask_client.get(f"/api/tasks/{task_id}/images/1", headers=VIEWER)

    assert resp.status_code == 200
    assert resp.mimetype == "image/gif"
    assert resp.data == GIF


def test_removing_an_image_never_reissues_its_index(flask_client):
    """`idx` is what a rendered thumbnail points at, so a freed one is retired
    rather than handed to the next paste."""
    task_id = _capture(flask_client)
    _attach(flask_client, task_id, PNG)
    _attach(flask_client, task_id, GIF)

    removed = flask_client.delete(f"/api/tasks/{task_id}/images/1",
                                  headers=EDITOR)
    replacement = _attach(flask_client, task_id, PNG)

    assert removed.status_code == 200
    assert replacement.get_json()["image"]["idx"] == 3
    assert [img["idx"] for img in images.list_for(task_id)] == [2, 3]
    assert flask_client.get(f"/api/tasks/{task_id}/images/1",
                            headers=VIEWER).status_code == 404


def test_removing_nothing_is_a_404(flask_client):
    task_id = _capture(flask_client)

    assert flask_client.delete(f"/api/tasks/{task_id}/images/7",
                               headers=EDITOR).status_code == 404


# ── auth ─────────────────────────────────────────────────────

def test_writes_need_an_editor_and_reads_need_a_login(flask_client,
                                                      anon_client):
    task_id = _capture(flask_client)
    _attach(flask_client, task_id)

    assert _attach(flask_client, task_id, headers=VIEWER).status_code == 403
    assert flask_client.delete(f"/api/tasks/{task_id}/images/1",
                               headers=VIEWER).status_code == 403
    assert anon_client.get(f"/api/tasks/{task_id}/images/1").status_code == 401
    assert len(images.list_for(task_id)) == 1


def test_image_routes_are_not_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS

    for endpoint in ("tasks.api_attach_task_image", "tasks.api_get_task_image",
                     "tasks.api_remove_task_image"):
        assert endpoint not in PUBLIC_API_ENDPOINTS


# ── editing after save ───────────────────────────────────────

def test_a_saved_task_can_be_edited_and_keeps_its_images(flask_client):
    task_id = _capture(flask_client, "first wording")
    _attach(flask_client, task_id, PNG)

    resp = flask_client.patch(f"/api/tasks/{task_id}",
                              json={"title": "second wording",
                                    "body": "more detail"}, headers=EDITOR)

    assert resp.status_code == 200
    task = resp.get_json()["task"]
    assert task["title"] == "second wording"
    assert [img["idx"] for img in task["images"]] == [1]


# ── delivery ─────────────────────────────────────────────────

@pytest.fixture
def launcher(monkeypatch):
    """A stubbed `launch_run`, recording every call."""
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id="sdk-fake",
                               add_done_callback=lambda _cb: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def test_a_run_carries_the_task_images_beside_its_prompt(flask_client,
                                                         launcher):
    task_id = _capture(flask_client, "look at this")
    _attach(flask_client, task_id, PNG)
    _attach(flask_client, task_id, GIF)
    flask_client.post(f"/api/tasks/{task_id}/arm", json={}, headers=EDITOR)

    resp = flask_client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)

    assert resp.status_code == 201, resp.get_json()
    call = launcher.calls[0]
    # The prompt stays the idea as captured — nothing names the images.
    assert call["prompt"].split("<finish>")[0].strip() == "look at this"
    assert call["images"] == [
        {"media_type": "image/png", "data_b64": _b64(PNG)},
        {"media_type": "image/gif", "data_b64": _b64(GIF)},
    ]


def test_a_second_attempt_re_reads_whatever_the_task_carries_now(flask_client,
                                                                 launcher):
    """The bytes are not claimed by the first run, so editing them between
    attempts is what the operator expects it to be."""
    task_id = _capture(flask_client)
    _attach(flask_client, task_id, PNG)
    flask_client.post(f"/api/tasks/{task_id}/arm", json={}, headers=EDITOR)
    flask_client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)
    runs.complete_run(runs.latest_attempt(task_id)["id"], status="done")

    flask_client.delete(f"/api/tasks/{task_id}/images/1", headers=EDITOR)
    _attach(flask_client, task_id, GIF)
    flask_client.post(f"/api/tasks/{task_id}/arm", json={}, headers=EDITOR)
    flask_client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)

    assert len(launcher.calls) == 2
    assert launcher.calls[0]["images"] == [
        {"media_type": "image/png", "data_b64": _b64(PNG)}]
    assert launcher.calls[1]["images"] == [
        {"media_type": "image/gif", "data_b64": _b64(GIF)}]


def test_a_task_with_no_images_launches_with_an_empty_list(flask_client,
                                                           launcher):
    task_id = _capture(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/arm", json={}, headers=EDITOR)

    flask_client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)

    assert launcher.calls[0]["images"] == []
