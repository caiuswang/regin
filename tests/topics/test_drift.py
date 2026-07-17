"""Phase 1 — mechanical drift detector (rename-follow).

Covers the pure rename parser, git rename detection (range + history chase),
topic-ref rewrite into the overlay (never the approved base graph), memory-body rewrite
(veracity untouched), the gated orchestrator, and reflect's rename-follow
upgrade (rename → rewrite, genuine deletion → flag).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.memory import get_store
from lib.memory.models import MemoryInput
from lib.memory.reflect import ReflectResult, _flag_stale_references
from lib.settings import settings
from lib.topics import drift
from lib.topics.core import load_local_graph, topic_split_dir, write_split_graph


def _commit(repo: Path, msg: str) -> None:
    subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", msg])


def _git_mv(repo: Path, old: str, new: str) -> None:
    (repo / new).parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "-C", str(repo), "mv", old, new])


def _write_graph(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z", "topics": topics})


def _base_files(repo: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(topic_split_dir(repo).glob("*.json"))}


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


# ── pure parser ───────────────────────────────────────────────


def test_parse_rename_status_maps_only_renames():
    lines = [
        "R100\told.py\tnew.py",
        "R087\ta/x.py\tb/x.py",
        "M\tunchanged.py",
        "A\tadded.py",
        "D\tdeleted.py",
    ]
    assert drift.parse_rename_status(lines) == {
        "old.py": "new.py", "a/x.py": "b/x.py"}


def test_parse_rename_status_empty():
    assert drift.parse_rename_status([]) == {}
    assert drift.parse_rename_status(["M\tfoo.py"]) == {}


# ── git rename detection ──────────────────────────────────────


def test_renames_between_detects_git_mv(fake_git_repo):
    repo = fake_git_repo
    (repo / "old.py").write_text("payload\n")
    _commit(repo, "add old")
    _git_mv(repo, "old.py", "new.py")
    _commit(repo, "rename")
    assert drift.renames_between(repo, "HEAD~1", "HEAD") == {"old.py": "new.py"}


def test_renames_between_no_rename_is_empty(fake_git_repo):
    repo = fake_git_repo
    (repo / "x.py").write_text("a\n")
    _commit(repo, "add x")
    assert drift.renames_between(repo, "HEAD~1", "HEAD") == {}


def test_renames_from_history_follows_chain(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("chain payload\n")
    _commit(repo, "add a")
    _git_mv(repo, "a.py", "b.py")
    _commit(repo, "a->b")
    _git_mv(repo, "b.py", "c.py")
    _commit(repo, "b->c")
    # a.py is long gone; history must chase a -> b -> c (and c exists now).
    assert drift.renames_from_history(repo, {"a.py"}) == {"a.py": "c.py"}


def test_renames_from_history_skips_missing_target(fake_git_repo):
    repo = fake_git_repo
    # A path never renamed → nothing to resolve.
    assert drift.renames_from_history(repo, {"never.py"}) == {}


# ── topic refs: overlay only ──────────────────────────────────


def test_rewrite_topic_refs_writes_overlay_not_base(fake_git_repo):
    repo = fake_git_repo
    (repo / "old.py").write_text("p\n")
    _commit(repo, "add")
    _write_graph(repo, {"t1": _topic([{"path": "old.py", "role": "implementation"}])})
    base_before = _base_files(repo)

    touched = drift.rewrite_topic_refs(repo, {"old.py": "new.py"})
    assert touched == ["t1"]
    # the base graph (human-approved) is byte-for-byte untouched.
    assert _base_files(repo) == base_before
    # the overlay carries the rewritten path with role preserved.
    overlay = load_local_graph(repo)
    refs = overlay["topics"]["t1"]["refs"]
    assert refs == [{"path": "new.py", "role": "implementation"}]


def test_rewrite_topic_refs_no_renames_noop(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    assert drift.rewrite_topic_refs(repo, {}) == []


# ── memory bodies: veracity untouched, idempotent ─────────────


def test_rewrite_memory_refs_rewrites_body_keeps_veracity():
    store = get_store()
    mid = store.remember(MemoryInput(
        body="See lib/old.py for the handler.", title="m", kind="lesson",
        veracity="true", scope="repo:demo"))

    n = drift.rewrite_memory_refs(store, {"lib/old.py": "lib/new.py"})
    assert n == 1
    row = store.get(mid)
    assert "lib/new.py" in row.body and "lib/old.py" not in row.body
    assert row.veracity == "true"  # rename ≠ staleness


def test_rewrite_memory_body_respects_path_boundaries():
    store = get_store()
    # Body names the renamed path AND three colliding non-targets that must
    # survive untouched: a superstring dir, a suffix-extended path, and a
    # different file whose name embeds the old path.
    mid = store.remember(MemoryInput(
        body=("Edit src/app.py here (see src/app.pyc, tests/src/app.py, "
              "and xsrc/app.py)."),
        title="m", kind="lesson", scope="repo:demo"))

    assert drift.rewrite_memory_refs(store, {"src/app.py": "src/main.py"}) == 1
    body = store.get(mid).body
    assert "Edit src/main.py here" in body  # the exact standalone path rewritten
    assert "Edit src/app.py" not in body    # ...and only that one
    assert "src/app.pyc" in body            # suffix-extended path untouched
    assert "tests/src/app.py" in body       # superstring-dir path untouched
    assert "xsrc/app.py" in body            # embedded-name path untouched


def test_rewrite_memory_refs_is_idempotent():
    store = get_store()
    store.remember(MemoryInput(body="path lib/old.py here", title="m",
                               kind="lesson", scope="repo:demo"))
    assert drift.rewrite_memory_refs(store, {"lib/old.py": "lib/new.py"}) == 1
    # second pass: the old path is gone, nothing to follow.
    assert drift.rewrite_memory_refs(store, {"lib/old.py": "lib/new.py"}) == 0


# ── orchestrator gating (defaults-off) ────────────────────────


def test_run_mechanical_drift_disabled_is_noop(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", False)
    repo = fake_git_repo
    (repo / "old.py").write_text("p\n")
    _commit(repo, "add")
    _git_mv(repo, "old.py", "new.py")
    _commit(repo, "rename")
    _write_graph(repo, {"t1": _topic([{"path": "old.py"}])})

    result = drift.run_mechanical_drift(repo)
    assert result["enabled"] is False
    assert load_local_graph(repo)["topics"] == {}  # no overlay write


def test_run_mechanical_drift_enabled_rewrites(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", True)
    repo = fake_git_repo
    (repo / "old.py").write_text("p\n")
    _commit(repo, "add")
    _git_mv(repo, "old.py", "new.py")
    _commit(repo, "rename")
    _write_graph(repo, {"t1": _topic([{"path": "old.py"}])})

    result = drift.run_mechanical_drift(repo)
    assert result["enabled"] is True
    assert result["renames"] == 1
    assert result["topics_rewritten"] == 1
    assert load_local_graph(repo)["topics"]["t1"]["refs"] == [{"path": "new.py"}]


# ── reflect rename-follow upgrade ─────────────────────────────


def _stale_memory(repo: Path, body: str, monkeypatch) -> tuple:
    monkeypatch.setattr(settings.agent_memory, "verify_stale_refs", True)
    monkeypatch.setattr(settings, "repo_paths", [repo])
    store = get_store()
    mid = store.remember(MemoryInput(
        body=body, title="m", kind="lesson", veracity="true",
        scope=f"repo:{repo.name}"))
    rows = store.list_memories(status="active", include_tests=True, limit=100)
    return store, mid, rows


def test_reflect_renames_instead_of_flagging(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", True)
    repo = fake_git_repo
    (repo / "lib").mkdir(exist_ok=True)
    (repo / "lib" / "old.py").write_text("payload\n")
    _commit(repo, "add lib/old")
    _git_mv(repo, "lib/old.py", "lib/new.py")
    _commit(repo, "rename lib")

    store, mid, rows = _stale_memory(repo, "Handler is in lib/old.py.", monkeypatch)
    result = ReflectResult()
    _flag_stale_references(store, rows, dry_run=False, result=result)

    assert result.ref_renames == 1
    assert result.flagged_stale == 0
    row = store.get(mid)
    assert "lib/new.py" in row.body
    assert row.veracity == "true"  # rewritten, not demoted


def test_reflect_flags_genuine_deletion(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", True)
    repo = fake_git_repo
    store, mid, rows = _stale_memory(
        repo, "Logic lives in lib/gone.py somewhere.", monkeypatch)
    result = ReflectResult()
    _flag_stale_references(store, rows, dry_run=False, result=result)

    assert result.ref_renames == 0
    assert result.flagged_stale == 1
    assert store.get(mid).veracity == "unknown"  # true → unknown demote
