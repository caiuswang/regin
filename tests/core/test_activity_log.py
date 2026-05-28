"""Unit tests for lib.activity_log.

Verifies the read/write convention enforcement, single-stream feature
tagging, secret redaction, unknown-feature routing, bound context
propagation, and concurrent-write safety.

Rotation/retention behavior — see test_activity_log_rotation.py.
"""

from __future__ import annotations

import json
import threading
import warnings
from pathlib import Path

import pytest

from lib import activity_log
from lib.settings import settings


# ── Fixture: isolated log dir ───────────────────────────────

@pytest.fixture
def tmp_log_dir(monkeypatch, tmp_path) -> Path:
    """Reset module state and point the sink at a fresh tmp dir.

    Uses `enqueue=False` so writes are synchronous and assertable
    without calling loguru's `.complete()` after each line."""
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("REGIN_LOG_LEVEL", "DEBUG")  # so read() is visible
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    monkeypatch.setattr(activity_log, "_WARNED_FEATURES", set())
    activity_log.configure_activity_log(
        log_dir=tmp_path, enqueue=False, force=True,
    )
    return tmp_path


def _read_records(path: Path) -> list[dict]:
    """Parse a loguru `serialize=True` log file into record dicts."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line)["record"])
    return out


def _regin_log(tmp_log_dir: Path) -> Path:
    return tmp_log_dir / "regin.log"


# ── Level routing ──────────────────────────────────────────

def test_write_emits_info_record(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    log.write("pattern_imported", pattern_id="p1")
    records = _read_records(_regin_log(tmp_log_dir))
    assert len(records) == 1
    assert records[0]["level"]["name"] == "INFO"
    assert records[0]["message"] == "pattern_imported"
    assert records[0]["extra"]["pattern_id"] == "p1"
    assert records[0]["extra"]["feature"] == "patterns"


def test_read_emits_debug_record(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    log.read("pattern_loaded", pattern_id="p1")
    records = _read_records(_regin_log(tmp_log_dir))
    assert len(records) == 1
    assert records[0]["level"]["name"] == "DEBUG"


def test_error_emits_error_record(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    log.error("import_failed", reason="malformed_yaml")
    records = _read_records(_regin_log(tmp_log_dir))
    assert len(records) == 1
    assert records[0]["level"]["name"] == "ERROR"
    assert records[0]["extra"]["reason"] == "malformed_yaml"


def test_warn_emits_warning_record(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    log.warn("deprecated_field_used", field="legacy_id")
    records = _read_records(_regin_log(tmp_log_dir))
    assert len(records) == 1
    assert records[0]["level"]["name"] == "WARNING"


# ── Convention enforcement ──────────────────────────────────

def test_info_method_raises_attribute_error(tmp_log_dir):
    """log.info(...) must be a hard error so the convention can't be
    violated by accident. read/write are the only valid level methods."""
    log = activity_log.get_activity_logger("patterns")
    with pytest.raises(AttributeError):
        log.info("nope")  # type: ignore[attr-defined]


def test_debug_method_raises_attribute_error(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    with pytest.raises(AttributeError):
        log.debug("nope")  # type: ignore[attr-defined]


# ── Single-stream feature tagging ──────────────────────────

def test_features_share_one_file_with_tag(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("a")
    activity_log.get_activity_logger("hooks").write("b")
    # Only the single regin.log exists; no per-feature files.
    log_files = sorted(p.name for p in tmp_log_dir.iterdir()
                       if p.is_file() and p.suffix == ".log")
    assert log_files == ["regin.log"]
    records = _read_records(_regin_log(tmp_log_dir))
    by_feature = {r["extra"]["feature"]: r["message"] for r in records}
    assert by_feature == {"patterns": "a", "hooks": "b"}


# ── Unknown feature routing ────────────────────────────────

def test_unknown_feature_routes_to_other_and_warns(tmp_log_dir):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        log = activity_log.get_activity_logger("totally_made_up")
        log.write("event_one")
        # Second call must not re-warn.
        activity_log.get_activity_logger("totally_made_up").write("event_two")
    records = _read_records(_regin_log(tmp_log_dir))
    other = [r for r in records if r["extra"]["feature"] == "other"]
    assert [r["message"] for r in other] == ["event_one", "event_two"]
    unknown_warnings = [w for w in caught if "totally_made_up" in str(w.message)]
    assert len(unknown_warnings) == 1


# ── Redaction ──────────────────────────────────────────────

def test_redaction_strips_secret_keys(tmp_log_dir):
    log = activity_log.get_activity_logger("auth")
    log.write(
        "login_attempt",
        user="alice",
        password="hunter2",
        api_key="sk-abc",
        token="t-xyz",
        cookie="session=foo",
    )
    text = _regin_log(tmp_log_dir).read_text()
    assert "hunter2" not in text
    assert "sk-abc" not in text
    assert "t-xyz" not in text
    assert "session=foo" not in text
    assert "<redacted>" in text
    # Non-secret field passes through.
    record = json.loads(text.splitlines()[0])["record"]
    assert record["extra"]["user"] == "alice"


def test_redaction_is_case_insensitive(tmp_log_dir):
    log = activity_log.get_activity_logger("auth")
    log.write("event", Authorization="Bearer xyz", PASSWORD="p")
    text = _regin_log(tmp_log_dir).read_text()
    assert "Bearer xyz" not in text
    assert '"PASSWORD": "<redacted>"' in text


# ── Bound context ──────────────────────────────────────────

def test_bind_propagates_context_to_subsequent_writes(tmp_log_dir):
    base = activity_log.get_activity_logger("web")
    child = base.bind(request_id="req-42", user_id="u-7")
    child.write("http_request", path="/api/health", status=200)
    records = _read_records(_regin_log(tmp_log_dir))
    assert records[0]["extra"]["request_id"] == "req-42"
    assert records[0]["extra"]["user_id"] == "u-7"
    assert records[0]["extra"]["path"] == "/api/health"


def test_bind_returns_new_instance(tmp_log_dir):
    base = activity_log.get_activity_logger("web")
    child = base.bind(request_id="r1")
    assert isinstance(child, activity_log.ActivityLogger)
    assert child is not base


# ── Feature path + inventory ───────────────────────────────

def test_feature_path_returns_the_single_log(tmp_log_dir):
    # The argument is preserved for back-compat but every feature
    # routes to the same file now.
    assert activity_log.feature_path("patterns") == tmp_log_dir / "regin.log"
    assert activity_log.feature_path("hooks") == tmp_log_dir / "regin.log"


def test_log_path_returns_single_stream(tmp_log_dir):
    assert activity_log.log_path() == tmp_log_dir / "regin.log"


def test_iter_features_buckets_by_feature(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("e1")
    activity_log.get_activity_logger("patterns").write("e2")
    activity_log.get_activity_logger("hooks").write("h1")
    activity_log.get_activity_logger("hooks").error("h_err")
    infos = activity_log.iter_features()
    by_feature = {i.feature: i for i in infos}
    assert by_feature["patterns"].event_count == 2
    assert by_feature["patterns"].error_count == 0
    assert by_feature["hooks"].event_count == 2
    assert by_feature["hooks"].error_count == 1
    # Untouched features don't show up.
    assert "auth" not in by_feature


def test_iter_features_empty_when_no_writes(tmp_log_dir):
    # File may exist (loguru opens it eagerly) but should have no rows.
    assert activity_log.iter_features() == []


# ── Concurrency ────────────────────────────────────────────

def test_concurrent_writes_produce_valid_json_lines(tmp_log_dir):
    """8 threads × 100 writes = 800 JSON lines, none corrupted.

    enqueue=False keeps the test synchronous but still exercises
    loguru's per-record locking under thread contention. All writes
    land in the single regin.log."""
    log = activity_log.get_activity_logger("hooks")

    def _worker(tid: int):
        for i in range(100):
            log.write("handler_dispatched", thread=tid, seq=i)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = _read_records(_regin_log(tmp_log_dir))
    assert len(records) == 800
    # Every record carries its thread + seq cleanly — no torn writes.
    seen = {(r["extra"]["thread"], r["extra"]["seq"]) for r in records}
    assert len(seen) == 800


# ── Disabled mode ──────────────────────────────────────────

def test_disabled_env_skips_sink_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DISABLED", "1")
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    activity_log.configure_activity_log(log_dir=tmp_path, force=True)
    # No sink registered; calling write() is a silent no-op (no file created).
    log = activity_log.get_activity_logger("patterns")
    log.write("event_lost")
    assert not (tmp_path / "regin.log").exists()
