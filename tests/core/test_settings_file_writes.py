"""save_settings writes are read-modify-writes of a JSON file shared by the
server, the CLI and request threads: they must be serialized and atomic."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import threading

import pytest

from lib import settings as _cfg
from lib.settings import save_settings, settings_file_lock


@pytest.fixture
def paths(tmp_path, monkeypatch):
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(shared))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(local))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return {"shared": shared, "local": local}


def test_a_reader_during_a_write_sees_the_previous_file(paths, monkeypatch):
    paths["local"].write_text(json.dumps({"repo_paths": ["/a"]}))
    seen = []
    real_dump = json.dump

    def dump(obj, fh, **kw):
        seen.append(_cfg._load_json(str(paths["local"])))
        return real_dump(obj, fh, **kw)

    monkeypatch.setattr(_cfg.json, "dump", dump)
    save_settings({"other": 1}, scope="local")

    assert seen == [{"repo_paths": ["/a"]}]
    assert json.loads(paths["local"].read_text()) == {"repo_paths": ["/a"], "other": 1}


def test_save_waits_for_a_lock_holder_in_another_process(paths, monkeypatch):
    """The holder's own descriptor stands in for another process."""
    reached = threading.Event()
    real_flock = fcntl.flock
    real_load = _cfg._load_json

    def flock(fh, op):
        if threading.current_thread().name == "saver" and op == fcntl.LOCK_EX:
            reached.set()
        return real_flock(fh, op)

    def load(path):
        if threading.current_thread().name == "saver":
            reached.set()
        return real_load(path)

    monkeypatch.setattr(fcntl, "flock", flock)
    monkeypatch.setattr(_cfg, "_load_json", load)

    with open(str(paths["local"]) + ".lock", "a") as holder:
        real_flock(holder, fcntl.LOCK_EX)
        t = threading.Thread(target=save_settings, args=({"theirs": 1},),
                             kwargs={"scope": "local"}, name="saver")
        t.start()
        reached.wait()
        paths["local"].write_text(json.dumps({"ours": 1}))
        real_flock(holder, fcntl.LOCK_UN)
    t.join()

    assert json.loads(paths["local"].read_text()) == {"ours": 1, "theirs": 1}


def test_save_inside_a_held_lock_does_not_deadlock(paths):
    with settings_file_lock():
        save_settings({"k": 1}, scope="local")
    assert json.loads(paths["local"].read_text()) == {"k": 1}


def test_save_keeps_the_file_mode_and_a_symlinked_target(paths, tmp_path):
    target = tmp_path / "real.json"
    target.write_text("{}")
    os.chmod(target, 0o640)
    paths["local"].symlink_to(target)

    save_settings({"k": 1}, scope="local")

    assert paths["local"].is_symlink()
    assert json.loads(target.read_text()) == {"k": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
