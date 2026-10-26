"""Comment images reaching a run.

An open thread's comment images ride the launch after the task's own, up to
`prompt_images_max_count`; the ones past the ceiling are named on their
comment's `<followup>` line with the `image_get` call that fetches them. A
resolved thread contributes nothing either way.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from lib.comments import images as comment_images
from lib.comments import store as comments
from lib.settings import settings
from lib.tasks import images as task_images
from lib.tasks import runs


def _png(tag: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes([tag]) * 32


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _armed(client) -> int:
    resp = client.post("/api/tasks", json={"title": "t", "body": "do it"})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task"]["id"]
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    return task_id


def _comment(task_id: int, body: str, pictures: dict[int, bytes],
             resolved: bool = False) -> int:
    staged = comment_images.check_post(body, [
        {"idx": idx, "data_b64": base64.b64encode(data).decode("ascii")}
        for idx, data in pictures.items()])
    thread = comments.open_thread("task", str(task_id))
    comment = comments.add_comment(thread["id"], body=body, images=staged)
    if resolved:
        comments.resolve(thread["id"], "resolved")
    return comment["id"]


def _launch(client, launcher, task_id) -> dict:
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[-1]


def _sent(call) -> list[bytes]:
    return [base64.b64decode(image["data_b64"])
            for image in call.get("images") or []]


def test_launch_sends_open_thread_comment_images(flask_client, launcher):
    task_id = _armed(flask_client)
    task_images.attach(task_id, _png(1))
    _comment(task_id, "open ![](image:1)", {1: _png(2)})
    _comment(task_id, "done ![](image:1)", {1: _png(3)}, resolved=True)

    call = _launch(flask_client, launcher, task_id)

    assert _sent(call) == [_png(1), _png(2)]
    assert "image_get(owner=\"comment\"" not in call["prompt"]


def test_launch_lists_overflow_comment_images_as_fetchable(
        flask_client, launcher, monkeypatch):
    monkeypatch.setattr(settings, "prompt_images_max_count", 2)
    task_id = _armed(flask_client)
    task_images.attach(task_id, _png(1))
    task_images.attach(task_id, _png(2))
    cid = _comment(task_id, "see the picture", {1: _png(3)})

    call = _launch(flask_client, launcher, task_id)

    assert _sent(call) == [_png(1), _png(2)]
    line = next(row for row in call["prompt"].splitlines()
                if "see the picture" in row)
    assert line.endswith(
        f"(image:1 — image_get(owner=\"comment\", owner_id={cid}, idx=1))")


def test_comment_images_fill_the_room_in_thread_comment_idx_order(
        flask_client, launcher, monkeypatch):
    monkeypatch.setattr(settings, "prompt_images_max_count", 3)
    task_id = _armed(flask_client)
    task_images.attach(task_id, _png(1))
    first = _comment(task_id, "a", {2: _png(3), 1: _png(2)})
    second = _comment(task_id, "b", {1: _png(4)})

    call = _launch(flask_client, launcher, task_id)

    assert _sent(call) == [_png(1), _png(2), _png(3)]
    assert f"owner_id={second}, idx=1" in call["prompt"]
    assert f"owner_id={first}" not in call["prompt"]
