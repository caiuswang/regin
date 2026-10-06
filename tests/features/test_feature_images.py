"""Images attached to a feature — storage, the routes, and delivery.

The invariant that separates `feature_images` from `agent_task_images` is
delivery: a task's own images ride beside its launch prompt, a feature's never
do. The spec cites them as `![](image:N)` and the run fetches the one it needs
(`regin feature image`), so a run of a feature-attributed task never pays for
mockups it did not cite (#257).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from lib.auth import create_token
from lib.features import images as feature_images
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.tasks import runs

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 16
NOT_AN_IMAGE = b"<svg>nope</svg>"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _feature(client, title="a feature", **body):
    resp = client.post("/api/features", json={"title": title, **body},
                       headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["id"]


def _attach(client, feature_id, data=PNG, headers=EDITOR):
    return client.post(f"/api/features/{feature_id}/images",
                       json={"data_b64": _b64(data)}, headers=headers)


def _task(client, title="a slice", **body):
    resp = client.post("/api/tasks", json={"title": title, **body},
                       headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


# ── attaching ────────────────────────────────────────────────

def test_attaching_numbers_images_in_order_and_serves_them_on_the_ledger(
        flask_client):
    feature_id = _feature(flask_client)

    first = _attach(flask_client, feature_id, PNG)
    second = _attach(flask_client, feature_id, GIF)

    assert first.status_code == 201, first.get_json()
    assert first.get_json()["image"]["idx"] == 1
    assert first.get_json()["image"]["media_type"] == "image/png"
    assert second.get_json()["image"]["idx"] == 2
    assert second.get_json()["image"]["media_type"] == "image/gif"

    ledger = flask_client.get(f"/api/features/{feature_id}",
                              headers=EDITOR).get_json()
    assert [img["idx"] for img in ledger["images"]] == [1, 2]
    # Invariant 4: a read never inlines the bytes.
    assert all("bytes" not in img and "data_b64" not in img
               for img in ledger["images"])


def test_the_media_type_comes_from_the_bytes_not_the_caller(flask_client):
    feature_id = _feature(flask_client)

    resp = _attach(flask_client, feature_id, NOT_AN_IMAGE)

    assert resp.status_code == 400
    assert "not a supported image" in resp.get_json()["error"]
    assert feature_images.list_for(feature_id) == []


def test_a_malformed_body_is_a_400_naming_the_field(flask_client):
    feature_id = _feature(flask_client)

    empty = flask_client.post(f"/api/features/{feature_id}/images", json={},
                              headers=EDITOR)
    garbage = flask_client.post(f"/api/features/{feature_id}/images",
                                json={"data_b64": "not base64!!"},
                                headers=EDITOR)

    assert empty.status_code == 400
    assert empty.get_json()["field"] == "data_b64"
    assert garbage.status_code == 400
    assert "valid base64" in garbage.get_json()["error"]


def test_an_oversized_image_is_refused_with_the_ceiling(flask_client,
                                                        monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings, "prompt_image_max_bytes", 16)
    feature_id = _feature(flask_client)

    resp = _attach(flask_client, feature_id, PNG)

    assert resp.status_code == 400
    assert "larger than 16 bytes" in resp.get_json()["error"]


def test_the_per_feature_count_ceiling_is_enforced(flask_client, monkeypatch):
    from lib.settings import settings

    monkeypatch.setattr(settings, "prompt_images_max_count", 2)
    feature_id = _feature(flask_client)

    assert _attach(flask_client, feature_id).status_code == 201
    assert _attach(flask_client, feature_id).status_code == 201
    third = _attach(flask_client, feature_id)

    assert third.status_code == 400
    assert "at most 2 images per feature" in third.get_json()["error"]


def test_attaching_to_an_unknown_feature_is_a_404(flask_client):
    assert _attach(flask_client, 999).status_code == 404


# ── reading and removing ─────────────────────────────────────

def test_the_bytes_come_back_with_their_own_media_type(flask_client):
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, WEBP)

    resp = flask_client.get(f"/api/features/{feature_id}/images/1",
                            headers=VIEWER)

    assert resp.status_code == 200
    assert resp.mimetype == "image/webp"
    assert resp.data == WEBP


def test_removing_an_image_never_reissues_its_index(flask_client):
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)
    _attach(flask_client, feature_id, GIF)

    removed = flask_client.delete(f"/api/features/{feature_id}/images/1",
                                  headers=EDITOR)
    replacement = _attach(flask_client, feature_id, PNG)

    assert removed.status_code == 200
    assert replacement.get_json()["image"]["idx"] == 3
    assert [img["idx"] for img in feature_images.list_for(feature_id)] == [2, 3]
    assert flask_client.get(f"/api/features/{feature_id}/images/1",
                            headers=VIEWER).status_code == 404


def test_removing_the_top_image_never_reissues_its_index(flask_client):
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)
    _attach(flask_client, feature_id, GIF)

    flask_client.delete(f"/api/features/{feature_id}/images/2", headers=EDITOR)
    replacement = _attach(flask_client, feature_id, PNG)

    assert replacement.get_json()["image"]["idx"] == 3

def test_removing_nothing_is_a_404(flask_client):
    feature_id = _feature(flask_client)

    assert flask_client.delete(f"/api/features/{feature_id}/images/7",
                               headers=EDITOR).status_code == 404


# ── auth ─────────────────────────────────────────────────────

def test_writes_need_an_editor_and_reads_need_a_login(flask_client,
                                                      anon_client):
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id)

    assert _attach(flask_client, feature_id, headers=VIEWER).status_code == 403
    assert flask_client.delete(f"/api/features/{feature_id}/images/1",
                               headers=VIEWER).status_code == 403
    assert flask_client.get(f"/api/features/{feature_id}/images/1",
                            headers=VIEWER).status_code == 200
    assert anon_client.get(
        f"/api/features/{feature_id}/images/1").status_code == 401
    assert len(feature_images.list_for(feature_id)) == 1


def test_feature_image_routes_are_not_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS

    for endpoint in ("features.api_attach_feature_image",
                     "features.api_get_feature_image",
                     "features.api_remove_feature_image"):
        assert endpoint not in PUBLIC_API_ENDPOINTS


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


def _run(client, task_id):
    client.post(f"/api/tasks/{task_id}/arm", json={}, headers=EDITOR)
    return client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)


@pytest.mark.parametrize("kind", ["task", "fix", "spec", "build"])
def test_a_run_carries_only_its_tasks_own_images(flask_client, launcher,
                                                 kind):
    """#257: whatever the kind, the feature's mockups stay off the launch."""
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)
    task_id = _task(flask_client, feature_id=feature_id)
    with SessionLocal() as session:
        session.get(AgentTask, task_id).kind = kind
        session.commit()
    flask_client.post(f"/api/tasks/{task_id}/images",
                      json={"data_b64": _b64(GIF)}, headers=EDITOR)

    _run(flask_client, task_id)

    assert launcher.calls[0]["images"] == [
        {"media_type": "image/gif", "data_b64": _b64(GIF)}]


def test_the_prompt_names_the_feature_images_and_how_to_fetch_one(
        flask_client, launcher):
    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)
    _attach(flask_client, feature_id, GIF)
    task_id = _task(flask_client, feature_id=feature_id, kind="build")

    _run(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert "(the feature holds: 1, 2)" in prompt
    assert f"feature image {feature_id} '<N>'" in prompt
    assert prompt.index("(the feature holds: 1, 2)") < prompt.index("</feature>")


def test_a_feature_without_images_adds_no_fetch_line(flask_client, launcher):
    task_id = _task(flask_client, feature_id=_feature(flask_client))

    _run(flask_client, task_id)

    assert "feature image" not in launcher.calls[0]["prompt"]


def test_the_cli_writes_the_cited_image_and_prints_its_path(flask_client,
                                                           tmp_path):
    from typer.testing import CliRunner  # noqa: PLC0415

    from cli.commands.feature import feature_app  # noqa: PLC0415

    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)
    _attach(flask_client, feature_id, GIF)
    out = tmp_path / "shot.gif"

    result = CliRunner().invoke(
        feature_app, ["image", str(feature_id), "2", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(out)
    assert out.read_bytes() == GIF


def test_the_cli_names_the_images_that_exist_when_one_does_not(flask_client):
    from typer.testing import CliRunner  # noqa: PLC0415

    from cli.commands.feature import feature_app  # noqa: PLC0415

    feature_id = _feature(flask_client)
    _attach(flask_client, feature_id, PNG)

    result = CliRunner().invoke(feature_app, ["image", str(feature_id), "7"])

    assert result.exit_code == 2
    assert "has no image 7 (it has: 1)" in result.output


def test_a_task_with_no_feature_carries_only_its_own_images(flask_client,
                                                            launcher):
    task_id = _task(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/images",
                      json={"data_b64": _b64(GIF)}, headers=EDITOR)

    _run(flask_client, task_id)

    assert launcher.calls[0]["images"] == [
        {"media_type": "image/gif", "data_b64": _b64(GIF)}]
