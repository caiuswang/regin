"""Round-trip tests for portable proposal bundles (export → git → import).

"Another machine" is simulated by exporting, deleting the local run via
the existing delete path (ORM + disk), then importing — the per-test
`tmp_db` fixture already guarantees a fresh DB per test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from lib.topics import bootstrap, topic_split_dir
from lib.topics.proposals import (
    create_proposal_feedback_thread,
    create_proposal_run,
    delete_proposal_run,
    export_proposal_bundle,
    import_proposal_bundle,
    ignore_proposed_topic,
    list_proposal_feedback_threads,
    list_proposal_revisions,
    load_proposal,
    set_proposal_feedback_thread_resolution,
)


def _prepare_repo(fake_git_repo) -> None:
    (fake_git_repo / "service").mkdir(exist_ok=True)
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "fixture"])
    bootstrap(fake_git_repo)


def _seed_reviewed_proposal(repo) -> None:
    """A run with two revisions, an ignore review marker, and feedback
    threads in mixed resolution states."""
    from lib.topics.proposal_orm import orm_save_proposal

    create_proposal_run(repo, run_id="run1")
    proposal = load_proposal(repo, "run1")
    orm_save_proposal(repo, "run1", proposal, wiki="# Wiki v2\n",
                      append_revision=True, revision_kind="regenerated")
    ignore_proposed_topic(repo, "run1", "stub-topic")
    create_proposal_feedback_thread(
        repo, "run1", proposal_topic_id="stub-topic",
        anchor_kind="proposal_summary", body="tighten the intent")
    resolved = create_proposal_feedback_thread(
        repo, "run1", proposal_topic_id=None, body="overall looks fine")
    set_proposal_feedback_thread_resolution(
        repo, "run1", resolved["id"], resolution_state="resolved")


_THREAD_MACHINE_KEYS = ("id", "revision_id", "addressed_in_revision_id")


def _portable_threads(threads: list[dict]) -> list[dict]:
    out = []
    for thread in threads:
        t = {k: v for k, v in thread.items() if k not in _THREAD_MACHINE_KEYS}
        t["comments"] = [
            {k: v for k, v in c.items() if k != "id"} for c in t["comments"]]
        out.append(t)
    return out


def _portable_revisions(revisions: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k not in ("id", "parent_revision_id")}
            for r in revisions]


def test_export_import_round_trip_reproduces_reader_output(
        stub_proposal_provider, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    topics_before = load_proposal(fake_git_repo, "run1")["topics"]
    revisions_before = _portable_revisions(
        list_proposal_revisions(fake_git_repo, "run1"))
    threads_before = _portable_threads(
        list_proposal_feedback_threads(fake_git_repo, "run1"))
    graph_before = {p.name: p.read_text()
                    for p in sorted(topic_split_dir(fake_git_repo).glob("*.json"))}
    wiki_before = (
        fake_git_repo / ".regin/topics/proposals/run1/wiki.md").read_text()

    path = export_proposal_bundle(fake_git_repo, "run1")
    assert path == fake_git_repo / ".regin/topics/bundles/run1.json"
    delete_proposal_run(fake_git_repo, "run1")

    result = import_proposal_bundle(fake_git_repo, path)
    assert result == {
        "proposal_id": "run1", "revisions": 2, "threads": 2,
        "action": "created",
    }
    assert json.dumps(load_proposal(fake_git_repo, "run1")["topics"],
                      sort_keys=True) == json.dumps(topics_before, sort_keys=True)
    assert _portable_revisions(
        list_proposal_revisions(fake_git_repo, "run1")) == revisions_before
    assert _portable_threads(
        list_proposal_feedback_threads(fake_git_repo, "run1")) == threads_before
    # Import only seeds review state — approved graph and disk wiki intact.
    assert {p.name: p.read_text()
            for p in sorted(topic_split_dir(fake_git_repo).glob("*.json"))} == graph_before
    assert (fake_git_repo / ".regin/topics/proposals/run1/wiki.md"
            ).read_text() == wiki_before


def _assert_seeded_threads_preserved(threads: list[dict]) -> None:
    by_body = {t["comments"][0]["body"]: t for t in threads}
    anchored = by_body["tighten the intent"]
    assert anchored["resolution_state"] == "open"
    assert anchored["proposal_topic_id"] == "stub-topic"
    assert anchored["anchor_kind"] == "proposal_summary"
    assert anchored["created_by"] == "user"
    assert by_body["overall looks fine"]["resolution_state"] == "resolved"


def test_import_preserves_review_markers_and_thread_states(
        stub_proposal_provider, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    path = export_proposal_bundle(fake_git_repo, "run1")
    delete_proposal_run(fake_git_repo, "run1")

    import_proposal_bundle(fake_git_repo, path)

    topic = load_proposal(fake_git_repo, "run1")["topics"][0]
    assert topic["review_status"] == "ignored"
    assert topic["ignored_at"]
    revisions = list_proposal_revisions(fake_git_repo, "run1")
    assert [r["kind"] for r in revisions] == ["regenerated", "generated"]
    assert [r["revision_number"] for r in revisions] == [2, 1]
    _assert_seeded_threads_preserved(
        list_proposal_feedback_threads(fake_git_repo, "run1"))


def test_import_refuses_existing_run_without_force(
        stub_proposal_provider, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    path = export_proposal_bundle(fake_git_repo, "run1")

    result = import_proposal_bundle(fake_git_repo, path)

    assert result["action"] == "refused"
    assert "already exists" in result["message"]
    # Local state untouched by the refusal.
    assert len(list_proposal_revisions(fake_git_repo, "run1")) == 2
    assert len(list_proposal_feedback_threads(fake_git_repo, "run1")) == 2


def test_import_force_replaces_local_run_wholesale(
        stub_proposal_provider, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    path = export_proposal_bundle(fake_git_repo, "run1")
    create_proposal_feedback_thread(
        fake_git_repo, "run1", proposal_topic_id=None, body="local-only note")

    result = import_proposal_bundle(fake_git_repo, path, force=True)

    assert result["action"] == "replaced"
    assert result["revisions"] == 2
    assert result["threads"] == 2
    bodies = [t["comments"][0]["body"]
              for t in list_proposal_feedback_threads(fake_git_repo, "run1")]
    assert "local-only note" not in bodies
    assert len(bodies) == 2


def test_import_registers_repo_row_on_a_fresh_machine(
        stub_proposal_provider, fake_git_repo, tmp_path):
    """The consumer may never have run `add-repo` — import lazy-upserts
    the Repo row (resolve_or_create semantics)."""
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    out = tmp_path / "shared" / "run1.json"
    path = export_proposal_bundle(fake_git_repo, "run1", out_path=out)
    assert path == out
    delete_proposal_run(fake_git_repo, "run1")

    other_machine_repo = tmp_path / "othermachine"
    other_machine_repo.mkdir()
    result = import_proposal_bundle(other_machine_repo, path)

    assert result["action"] == "created"
    proposal = load_proposal(other_machine_repo, "run1")
    assert proposal["topics"][0]["id"] == "stub-topic"
    assert (other_machine_repo / ".regin/topics/proposals/run1/wiki.md").exists()


def test_cli_export_then_import_reports_success_and_refusal(
        stub_proposal_provider, fake_git_repo):
    from typer.testing import CliRunner

    from cli.commands import topics as topics_cmd

    runner = CliRunner()
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)

    exported = runner.invoke(topics_cmd.topics_app, [
        "proposal-export", "run1", "--repo", str(fake_git_repo)])
    assert exported.exit_code == 0, exported.output
    assert "Exported proposal run1" in exported.output

    bundle = str(fake_git_repo / ".regin/topics/bundles/run1.json")
    refused = runner.invoke(topics_cmd.topics_app, [
        "proposal-import", bundle, "--repo", str(fake_git_repo)])
    assert refused.exit_code == 1
    assert "already exists" in refused.output
    assert "--force" in refused.output

    forced = runner.invoke(topics_cmd.topics_app, [
        "proposal-import", bundle, "--force", "--repo", str(fake_git_repo)])
    assert forced.exit_code == 0, forced.output
    assert "Replaced local proposal run1" in forced.output
    assert "2 revision(s)" in forced.output


def test_cli_import_missing_bundle_fails_clearly(fake_git_repo):
    from typer.testing import CliRunner

    from cli.commands import topics as topics_cmd

    result = CliRunner().invoke(topics_cmd.topics_app, [
        "proposal-import", str(fake_git_repo / "nope.json"),
        "--repo", str(fake_git_repo)])
    assert result.exit_code == 1
    assert "bundle not found" in result.output


def test_gitignore_reincludes_bundle_json(fake_git_repo):
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy(repo_root / ".gitignore", fake_git_repo / ".gitignore")

    def ignored(rel: str) -> bool:
        return subprocess.run(
            ["git", "-C", str(fake_git_repo), "check-ignore", "-q", rel],
        ).returncode == 0

    assert not ignored(".regin/topics/bundles/20260101T000000Z.json")
    assert ignored(".regin/topics/bundles/notes.txt")
    assert ignored(".regin/topics/proposals/run1/topics.json")
    assert not ignored(".regin/topics/topics/alpha.json")
    assert not ignored(".regin/topics/topics/_meta.json")
    assert ignored(".regin/topics/topic.json")


def test_export_refuses_invalid_ids(fake_git_repo):
    import pytest
    from lib.topics import TopicGraphError

    for bad in ("run\x00x", "a" * 200, "run\nx"):
        with pytest.raises(TopicGraphError, match="invalid proposal id"):
            export_proposal_bundle(fake_git_repo, bad)


def test_export_refuses_in_flight_run(stub_proposal_provider, fake_git_repo):
    import pytest
    from lib.orm import SessionLocal
    from lib.orm.models import ProposalRun
    from lib.topics import TopicGraphError

    _prepare_repo(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    with SessionLocal() as s:
        run = s.get(ProposalRun, "run1")
        run.state = "running"
        run.completed_at = None
        s.commit()

    with pytest.raises(TopicGraphError, match="still in flight"):
        export_proposal_bundle(fake_git_repo, "run1")


def test_export_patches_target_repo_gitignore(stub_proposal_provider, fake_git_repo):
    """A managed repo that ignores `.regin/*` (the normal case outside
    regin itself) gets the bundle re-include patched in at export time,
    so the documented plain `git add` flow works."""
    _prepare_repo(fake_git_repo)
    (fake_git_repo / ".gitignore").write_text(
        ".regin/*\n!.regin/topics/\n.regin/topics/*\n"
        "!.regin/topics/topic.json\n")
    create_proposal_run(fake_git_repo, run_id="run1")

    path = export_proposal_bundle(fake_git_repo, "run1")

    assert "!.regin/topics/bundles/*.json" in (fake_git_repo / ".gitignore").read_text()
    ignored = subprocess.run(
        ["git", "-C", str(fake_git_repo), "check-ignore", "-q",
         str(path.relative_to(fake_git_repo))],
        capture_output=True).returncode == 0
    assert not ignored


def test_import_rejects_duplicate_revision_numbers(stub_proposal_provider, fake_git_repo):
    import pytest
    from lib.topics import TopicGraphError
    from lib.topics.proposals.core_io import load_proposal_status

    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    path = export_proposal_bundle(fake_git_repo, "run1")
    delete_proposal_run(fake_git_repo, "run1")

    bundle = json.loads(path.read_text())
    for r in bundle["revisions"]:
        r["revision_number"] = 1
    path.write_text(json.dumps(bundle))

    with pytest.raises(TopicGraphError, match="revision numbers"):
        import_proposal_bundle(fake_git_repo, path)
    # Refusal is transactional — no partial run row survives.
    with pytest.raises(TopicGraphError):
        load_proposal_status(fake_git_repo, "run1")


def test_import_collapses_multiple_is_latest(stub_proposal_provider, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _seed_reviewed_proposal(fake_git_repo)
    path = export_proposal_bundle(fake_git_repo, "run1")
    delete_proposal_run(fake_git_repo, "run1")

    bundle = json.loads(path.read_text())
    for r in bundle["revisions"]:
        r["is_latest"] = True
    path.write_text(json.dumps(bundle))

    import_proposal_bundle(fake_git_repo, path)
    revisions = list_proposal_revisions(fake_git_repo, "run1")
    assert sum(1 for r in revisions if r.get("is_latest")) == 1
    latest = max(revisions, key=lambda r: r["revision_number"])
    assert latest["is_latest"]
