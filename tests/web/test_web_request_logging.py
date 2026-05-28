"""Verifies that every Flask API call lands in the activity log tagged feature=web.

The activity-log web middleware is wired in `web/app.py::_install_request_logging`.
It runs `@before_request` / `@after_request` to stamp a request_id +
duration and writes one INFO row per non-static request into the
single-stream `regin.log`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import activity_log


@pytest.fixture
def web_log_client(monkeypatch, tmp_db, tmp_path) -> tuple:
    """Flask test client with activity-log redirected to tmp_path.

    Must reset module state BEFORE `create_app()` runs so the sinks
    register against the temp dir."""
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    monkeypatch.setattr(activity_log, "_WARNED_FEATURES", set())
    activity_log.configure_activity_log(
        log_dir=tmp_path, enqueue=False, force=True,
    )
    from web.app import create_app
    from lib.auth import create_token
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        # /api/ reads are gated (see web.app._install_auth_gate); authenticate.
        client.environ_base["HTTP_AUTHORIZATION"] = (
            f"Bearer {create_token(1, 'test-editor', 'editor')}"
        )
        yield client, tmp_path


def _read_web_records(log_dir: Path) -> list[dict]:
    """Pull records tagged `feature=web` from the single regin.log stream."""
    path = log_dir / "regin.log"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)["record"]
        if (rec.get("extra") or {}).get("feature") == "web":
            out.append(rec)
    return out


def test_api_request_emits_http_request_record(web_log_client):
    client, log_dir = web_log_client
    resp = client.get("/api/status")
    assert resp.status_code == 200
    records = _read_web_records(log_dir)
    http_records = [r for r in records if r["message"] == "http_request"]
    assert http_records, "no http_request record emitted"
    last = http_records[-1]
    assert last["extra"]["method"] == "GET"
    assert last["extra"]["path"] == "/api/status"
    assert last["extra"]["status"] == 200
    assert isinstance(last["extra"]["duration_ms"], (int, float))
    assert isinstance(last["extra"]["request_id"], str)


def test_request_log_includes_status_code_on_404(web_log_client):
    client, log_dir = web_log_client
    client.get("/api/this-route-does-not-exist")
    records = _read_web_records(log_dir)
    http_records = [r for r in records if r["message"] == "http_request"]
    assert any(r["extra"]["status"] == 404 for r in http_records)
