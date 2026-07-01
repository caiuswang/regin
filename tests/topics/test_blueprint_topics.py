"""API tests for repo-local topic graph endpoints."""

from __future__ import annotations

import json
import sqlite3
import subprocess

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import ProposalRun, ProposalTopic, Repo
from lib.topics import bootstrap, utc_now
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.proposals import create_proposal_run, regenerate_proposal_run, save_proposal


def _seed_repo_record(path, name="topic-repo"):
    with SessionLocal() as session:
        repo = Repo(name=name, path=str(path), default_branch="main", is_active=1)
        session.add(repo)
        session.commit()
    return name


def _editor_bearer() -> str:
    """Editor JWT for the inline test clients the bootstrap tests build
    by hand (the /api/ routes are gated; see web.app._install_auth_gate)."""
    from lib.auth import create_token
    return f"Bearer {create_token(1, 'test-editor', 'editor')}"


def test_topics_list_bootstraps_missing_graph(flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)

    resp = flask_client.get(f"/api/repos/{name}/topics")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["topics"] == []
    assert body["validation"]["ok"] is True


def test_topics_scan_endpoint_updates_refs(flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "web"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_authoritative_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web", "aliases": [], "intent": "Web.", "status": "active",
        "refs": [], "edges": [], "commands": [],
        "include_globs": ["web/**"], "exclude_globs": [],
    }
    from lib.topics import save_graph
    save_graph(fake_git_repo, graph)

    scan_resp = flask_client.post(f"/api/repos/{name}/topics/scan", json={})
    assert scan_resp.status_code == 200
    body = scan_resp.get_json()
    assert body["updated_topics"] == ["web"]
    assert body["covered_ref_count"] == 1


def test_topics_proposal_endpoints(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "proposal"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)

    providers_resp = flask_client.get(f"/api/repos/{name}/topics/proposal-providers")
    assert providers_resp.status_code == 200
    assert providers_resp.get_json()["providers"][0]["id"] == "external-agent"

    create_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"topic_request": "service boundaries"},
    )
    assert create_resp.status_code == 200
    proposal_id = create_resp.get_json()["proposal"]["id"]

    list_resp = flask_client.get(f"/api/repos/{name}/topics/proposals")
    assert list_resp.status_code == 200
    assert list_resp.get_json()["proposals"][0]["id"] == proposal_id

    detail_resp = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.get_json()
    assert detail["proposal"]["version"] == 1
    assert detail["proposal"]["repo"] == fake_git_repo.name
    assert detail["proposal"]["status"] == "pending_review"
    assert detail["status"]["state"] == "completed"
    assert "Stub Wiki" in detail["wiki"]

    summary_resp = flask_client.get(f"/api/repos/{name}/topics/workspace/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.get_json()
    assert summary["proposal_run_count"] >= 1
    assert summary["approved_topic_count"] >= 0

    workspace_resp = flask_client.get(f"/api/repos/{name}/topics/workspace/proposals", query_string={"proposal_id": proposal_id})
    assert workspace_resp.status_code == 200
    workspace = workspace_resp.get_json()
    assert workspace["selected_proposal_id"] == proposal_id
    assert workspace["selected_run"]["provider"] == "external-agent"
    assert workspace["selected_run"]["review_state"] == "pending_review"
    assert workspace["selected_run"]["revision_count"] >= 1
    assert workspace["runs"][0]["draft_topic_count"] >= 1
    assert workspace["revisions"][0]["revision_number"] == 1
    assert workspace["selected_revision"]["revision_number"] == 1
    assert workspace["wiki_preview"].startswith("# Stub Wiki")


def test_topics_workspace_can_select_historical_revision(stub_proposal_provider, flask_client, tmp_db, fake_git_repo, monkeypatch):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    proposal = flask_client.get(f"/api/repos/{name}/topics/proposals/run1").get_json()["proposal"]
    proposed_topic_id = proposal["topics"][0]["id"]
    create_thread_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/run1/feedback-threads",
        json={
            "proposal_topic_id": proposed_topic_id,
            "anchor_kind": "topic_field",
            "anchor": {"topic_id": proposed_topic_id, "field": "intent"},
            "quoted_text": proposal["topics"][0]["intent"],
            "body": "Please tighten the intent before applying.",
        },
    )
    assert create_thread_resp.status_code == 200

    def fake_draft(*, repo, out_dir, proposal_id, topic_request=None, scope="all", agent=None, prior_draft=None, prompt_templates=None):
        del repo, out_dir, proposal_id, topic_request, scope, agent, prompt_templates
        refreshed = dict(prior_draft["proposal"])
        refreshed["topics"] = [{
            "id": "service-regenerated",
            "label": "Service Regenerated",
            "aliases": [],
            "intent": "Refreshed.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": ["service/**"],
            "exclude_globs": [],
            "evidence_paths": [],
        }]
        return refreshed, "# Refreshed wiki\n"

    monkeypatch.setattr("lib.topics.proposals.external_jobs._draft_proposal", fake_draft)
    regenerate_proposal_run(fake_git_repo, "run1")
    latest_workspace = flask_client.get(
        f"/api/repos/{name}/topics/workspace/proposals",
        query_string={"proposal_id": "run1"},
    ).get_json()
    revisions = latest_workspace["revisions"]
    original_revision_id = revisions[-1]["id"]
    assert latest_workspace["feedback_threads"][0]["resolution_state"] == "addressed"
    assert latest_workspace["feedback_threads"][0]["revision_number"] == 1
    assert latest_workspace["feedback_threads"][0]["addressed_in_revision_number"] == 2

    workspace_resp = flask_client.get(
        f"/api/repos/{name}/topics/workspace/proposals",
        query_string={"proposal_id": "run1", "revision_id": original_revision_id},
    )

    assert workspace_resp.status_code == 200
    workspace = workspace_resp.get_json()
    assert workspace["selected_revision"]["id"] == original_revision_id
    assert workspace["selected_revision"]["is_latest"] is False
    assert workspace["draft_topics"][0]["label"] == "Stub Topic"
    assert workspace["wiki_preview"].startswith("# Stub Wiki")
    assert workspace["feedback_threads"][0]["resolution_state"] == "open"


def _post_ok(flask_client, url, payload=None):
    resp = flask_client.post(url, json=payload or {})
    assert resp.status_code == 200
    return resp.get_json()


def _seed_feedback_thread(flask_client, fake_git_repo):
    """Seed a repo + proposal run + one feedback thread; return
    (repo_name, thread_id, first_comment_id)."""
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    proposal = flask_client.get(f"/api/repos/{name}/topics/proposals/run1").get_json()["proposal"]
    topic_id = proposal["topics"][0]["id"]
    created = _post_ok(
        flask_client,
        f"/api/repos/{name}/topics/proposals/run1/feedback-threads",
        {
            "proposal_topic_id": topic_id,
            "anchor_kind": "topic_field",
            "anchor": {"topic_id": topic_id, "field": "intent"},
            "body": "Initial review note.",
        },
    )
    thread = created["feedback_thread"]
    assert thread["resolution_state"] == "open"
    return name, thread["id"], thread["comments"][0]["id"]


def test_workspace_runs_annotate_open_drift_notes(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    """A run carrying an open content_drift note is annotated with a non-zero
    `open_drift_note_count`; plain comments and resolved drift notes don't count."""
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    proposal = flask_client.get(f"/api/repos/{name}/topics/proposals/run1").get_json()["proposal"]
    topic_id = proposal["topics"][0]["id"]

    def _thread(kind, body):
        return _post_ok(
            flask_client,
            f"/api/repos/{name}/topics/proposals/run1/feedback-threads",
            {"proposal_topic_id": topic_id, "kind": kind, "author_kind": "agent", "body": body},
        )["feedback_thread"]

    # A plain comment must NOT count as drift.
    _thread("comment", "Just a review comment.")
    drift = _thread("content_drift", "The refs under this topic drifted.")

    def _run_row():
        runs = flask_client.get(
            f"/api/repos/{name}/topics/workspace/proposals",
            query_string={"proposal_id": "run1"},
        ).get_json()["runs"]
        return next(run for run in runs if run["id"] == "run1")

    row = _run_row()
    assert row["open_drift_note_count"] == 1
    assert topic_id in row["open_drift_topics"]

    # Resolving the drift note drops it from the count.
    _post_ok(
        flask_client,
        f"/api/repos/{name}/topics/proposals/run1/feedback-threads/{drift['id']}/resolution",
        {"resolution_state": "resolved"},
    )
    resolved_row = _run_row()
    assert resolved_row["open_drift_note_count"] == 0
    assert resolved_row["open_drift_topics"] == []


def test_topics_feedback_thread_resolution(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    name, thread_id, _ = _seed_feedback_thread(flask_client, fake_git_repo)
    base = f"/api/repos/{name}/topics/proposals/run1/feedback-threads/{thread_id}/resolution"

    resolved = _post_ok(flask_client, base, {"resolution_state": "resolved"})
    assert resolved["feedback_thread"]["resolution_state"] == "resolved"

    bad = flask_client.post(base, json={"resolution_state": "bogus"})
    assert bad.status_code == 400

    reopened = _post_ok(flask_client, base, {"resolution_state": "open"})
    assert reopened["feedback_thread"]["resolution_state"] == "open"


def test_topics_feedback_comment_edit_and_delete(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    name, thread_id, first_comment_id = _seed_feedback_thread(flask_client, fake_git_repo)
    threads = f"/api/repos/{name}/topics/proposals/run1/feedback-threads/{thread_id}"

    reply = _post_ok(flask_client, f"{threads}/comments", {"body": "Second comment."})
    reply_comment_id = reply["feedback_thread"]["comments"][-1]["id"]

    edited = _post_ok(flask_client, f"{threads}/comments/{first_comment_id}/update", {"body": "Edited note."})
    assert any(c["id"] == first_comment_id and c["body"] == "Edited note." for c in edited["feedback_thread"]["comments"])

    blank = flask_client.post(f"{threads}/comments/{first_comment_id}/update", json={"body": "   "})
    assert blank.status_code == 400

    kept = _post_ok(flask_client, f"{threads}/comments/{reply_comment_id}/delete")
    assert len(kept["feedback_thread"]["comments"]) == 1

    removed = _post_ok(flask_client, f"{threads}/comments/{first_comment_id}/delete")
    assert removed["deleted_thread"] is True


def test_topics_workspace_bootstraps_missing_proposal_tables(stub_proposal_provider, tmp_db, fake_git_repo):
    conn = sqlite3.connect(str(tmp_db))
    try:
        for table in (
            "proposal_feedback_comments",
            "proposal_feedback_threads",
            "proposal_revision_topics",
            "proposal_revisions",
            "proposal_topics",
            "proposal_runs",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()

    from web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)

    with app.test_client() as flask_client:
        flask_client.environ_base["HTTP_AUTHORIZATION"] = _editor_bearer()
        create_resp = flask_client.post(
            f"/api/repos/{name}/topics/proposals",
            json={"scope": "all", "provider": "langchain"},
        )
        assert create_resp.status_code == 200
        proposal_id = create_resp.get_json()["proposal"]["id"]

        workspace_resp = flask_client.get(
            f"/api/repos/{name}/topics/workspace/proposals",
            query_string={"proposal_id": proposal_id},
        )
        assert workspace_resp.status_code == 200
        assert workspace_resp.get_json()["selected_proposal_id"] == proposal_id


def test_topics_workspace_bootstraps_feedback_revision_columns(stub_proposal_provider, tmp_db, fake_git_repo):
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("DROP TABLE IF EXISTS proposal_feedback_comments")
        conn.execute("DROP TABLE IF EXISTS proposal_feedback_threads")
        conn.execute(
            """
            CREATE TABLE proposal_feedback_threads (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              TEXT NOT NULL REFERENCES proposal_runs(id) ON DELETE CASCADE,
                proposal_topic_id   TEXT,
                kind                TEXT NOT NULL DEFAULT 'comment',
                anchor_kind         TEXT NOT NULL DEFAULT 'general',
                anchor_json         TEXT NOT NULL DEFAULT '{}',
                quoted_text         TEXT,
                resolution_state    TEXT NOT NULL DEFAULT 'open',
                addressed_in_run_id TEXT,
                created_by          TEXT NOT NULL DEFAULT 'user',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                metadata_json       TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_run_id "
            "ON proposal_feedback_threads(run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_topic_id "
            "ON proposal_feedback_threads(proposal_topic_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_resolution "
            "ON proposal_feedback_threads(resolution_state)"
        )
        conn.execute(
            """
            CREATE TABLE proposal_feedback_comments (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_thread_id INTEGER NOT NULL REFERENCES proposal_feedback_threads(id) ON DELETE CASCADE,
                author_kind        TEXT NOT NULL DEFAULT 'user',
                body               TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL,
                metadata_json      TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    from web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)

    with app.test_client() as flask_client:
        flask_client.environ_base["HTTP_AUTHORIZATION"] = _editor_bearer()
        create_resp = flask_client.post(
            f"/api/repos/{name}/topics/proposals",
            json={"scope": "all", "provider": "langchain"},
        )
        assert create_resp.status_code == 200
        proposal_id = create_resp.get_json()["proposal"]["id"]
        proposal = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}").get_json()["proposal"]
        proposed_topic_id = proposal["topics"][0]["id"]

        create_thread_resp = flask_client.post(
            f"/api/repos/{name}/topics/proposals/{proposal_id}/feedback-threads",
            json={
                "proposal_topic_id": proposed_topic_id,
                "anchor_kind": "topic_field",
                "anchor": {"topic_id": proposed_topic_id, "field": "intent"},
                "quoted_text": proposal["topics"][0]["intent"],
                "body": "Please tighten the intent before applying.",
            },
        )
        assert create_thread_resp.status_code == 200
        assert create_thread_resp.get_json()["feedback_thread"]["revision_number"] == 1


def test_topics_workspace_prefers_reviewable_proposal_over_downgrade_run(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo, seeds=True)
    create_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"scope": "all", "provider": "langchain"},
    )
    proposal_id = create_resp.get_json()["proposal"]["id"]

    save_proposal(fake_git_repo, "zzzz-downgrade", {
        "version": 1,
        "repo": fake_git_repo.name,
        "provider": "approved-topic-downgrade",
        "scope": "all",
        "status": "pending_review",
        "generated_at": utc_now(),
        "topics": [{
            "id": "overview",
            "label": "Overview",
            "aliases": [],
            "intent": "Downgraded topic.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        }],
    })

    workspace_resp = flask_client.get(f"/api/repos/{name}/topics/workspace/proposals")

    assert workspace_resp.status_code == 200
    workspace = workspace_resp.get_json()
    assert workspace["selected_proposal_id"] == proposal_id
    assert workspace["selected_run"]["provider"] != "approved-topic-downgrade"


def test_topics_workspace_backfills_revision_for_legacy_run(flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    proposal_id = "legacy-run"
    proposal_dir = fake_git_repo / ".regin/topics/proposals" / proposal_id
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "wiki.md").write_text("# Legacy wiki\n")
    with SessionLocal() as session:
        repo = session.exec(select(Repo).where(Repo.name == name)).first()
        session.add(ProposalRun(
            id=proposal_id,
            repo_id=repo.id,
            provider="external-agent",
            scope="all",
            state="completed",
            started_at=utc_now(),
            completed_at=utc_now(),
            updated_at=utc_now(),
            prompt_template_slugs="[]",
            metadata_json='{"repo_name": "topic-repo", "proposal_status": "pending_review"}',
        ))
        session.add(ProposalTopic(
            run_id=proposal_id,
            topic_id="legacy-topic",
            label="Legacy Topic",
            intent="Legacy intent.",
            status="active",
            aliases_json="[]",
            refs_json="[]",
            edges_json="[]",
            commands_json="[]",
            include_globs_json="[]",
            exclude_globs_json="[]",
            evidence_paths_json="[]",
        ))
        session.commit()

    workspace_resp = flask_client.get(
        f"/api/repos/{name}/topics/workspace/proposals",
        query_string={"proposal_id": proposal_id},
    )

    assert workspace_resp.status_code == 200
    workspace = workspace_resp.get_json()
    assert workspace["selected_revision"]["revision_number"] == 1
    assert workspace["selected_revision"]["metadata"]["backfilled_from_legacy"] is True
    assert workspace["revisions"][0]["revision_number"] == 1


def test_topics_proposal_detail_and_status_include_failure_diagnostics(flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    proposal_id = "run1"
    proposal_dir = fake_git_repo / ".regin/topics/proposals" / proposal_id
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "topics.json").write_text(json.dumps({
        "version": 1,
        "repo": fake_git_repo.name,
        "scope": "all",
        "generated_at": utc_now(),
        "status": "draft",
        "topics": [],
        "provider": "external-agent",
    }))
    (proposal_dir / "wiki.md").write_text("# Draft wiki\n")
    (proposal_dir / "status.json").write_text(json.dumps({
        "state": "failed",
        "trace_id": "topic-proposal-run1",
        "agent": "codex",
        "error": "external agent exited with code 1: short summary",
        "error_detail": "full provider billing failure detail",
        "stdout_tail": "stdout tail",
        "stderr_tail": "stderr tail",
    }))

    detail_resp = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.get_json()
    assert detail["status"]["error_detail"] == "full provider billing failure detail"
    assert detail["status"]["stdout_tail"] == "stdout tail"
    assert detail["status"]["stderr_tail"] == "stderr tail"

    status_resp = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}/status")
    assert status_resp.status_code == 200
    status = status_resp.get_json()["status"]
    assert status["error_detail"] == "full provider billing failure detail"
    assert status["stdout_tail"] == "stdout tail"
    assert status["stderr_tail"] == "stderr tail"


def test_topics_proposal_topic_update_endpoint(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "proposal"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]
    detail = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}").get_json()["proposal"]
    proposed_topic_id = detail["topics"][0]["id"]

    update_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{proposed_topic_id}",
        json={"label": "Service Layer", "aliases": ["svc"], "include_globs": ["service/**"]},
    )

    assert update_resp.status_code == 200
    body = update_resp.get_json()
    assert body["topic"]["label"] == "Service Layer"
    assert body["topic"]["aliases"] == ["svc"]


def test_topics_proposal_regenerate_endpoint(stub_proposal_provider, flask_client, tmp_db, fake_git_repo, monkeypatch):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]

    def fake_regenerate(repo_path, run_id):
        from lib.topics.proposals import load_proposal, save_proposal
        proposal_dir = fake_git_repo / ".regin/topics/proposals" / run_id
        proposal = load_proposal(fake_git_repo, run_id)
        proposal["topics"] = [{
            "id": "service",
            "label": "Service Layer",
            "aliases": [],
            "intent": "Service.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        }]
        save_proposal(fake_git_repo, run_id, proposal)
        (proposal_dir / "wiki.md").write_text("# Regenerated wiki\n")
        return {
            "dir": proposal_dir,
            "evidence": proposal_dir / "evidence.json",
            "topics": proposal_dir / "topics.json",
            "wiki": proposal_dir / "wiki.md",
        }

    monkeypatch.setattr("web.blueprints.topics.regenerate_proposal_run", fake_regenerate)

    resp = flask_client.post(f"/api/repos/{name}/topics/proposals/{proposal_id}/regenerate", json={})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["proposal"]["topics"][0]["label"] == "Service Layer"
    assert body["wiki"] == "# Regenerated wiki\n"


def test_topics_proposal_regenerate_endpoint_reports_provider_error(
    flask_client, tmp_db, fake_git_repo, monkeypatch
):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    proposal_dir = fake_git_repo / ".regin/topics/proposals/run1"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "evidence.json").write_text(json.dumps({"repo": fake_git_repo.name, "scope": "all"}))
    (proposal_dir / "wiki.md").write_text("# Draft wiki\n")
    save_proposal(fake_git_repo, "run1", {
        "version": 1,
        "repo": fake_git_repo.name,
        "provider": "langchain",
        "scope": "all",
        "status": "pending_review",
        "generated_at": utc_now(),
        "metadata": {},
        "topics": [{
            "id": "service",
            "label": "Service",
            "aliases": [],
            "intent": "Service.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        }],
    })

    monkeypatch.setattr(
        "web.blueprints.topics.regenerate_proposal_run",
        lambda repo_path, proposal_id: (_ for _ in ()).throw(
            ValueError("provider HTTP 503 from https://example.test/v1/chat/completions: model not found")
        ),
    )

    resp = flask_client.post(f"/api/repos/{name}/topics/proposals/run1/regenerate", json={})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "provider HTTP 503" in body["error"]


def test_topics_external_agent_regenerate_endpoint_returns_without_blocking(flask_client, tmp_db, fake_git_repo, monkeypatch):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    proposal_dir = fake_git_repo / ".regin/topics/proposals/run1"
    proposal_dir.mkdir(parents=True)
    (proposal_dir / "evidence.json").write_text(json.dumps({"repo": fake_git_repo.name, "scope": "all"}))
    (proposal_dir / "wiki.md").write_text("# Draft wiki\n")
    save_proposal(fake_git_repo, "run1", {
        "version": 1,
        "repo": fake_git_repo.name,
        "provider": "external-agent",
        "scope": "all",
        "status": "pending_review",
        "generated_at": utc_now(),
        "metadata": {"agent": "claude", "prompt_template_ids": []},
        "topics": [{
            "id": "service",
            "label": "Service",
            "aliases": [],
            "intent": "Service.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        }],
    })

    called = {}

    def fake_start(repo_path, proposal_id):
        called["repo_path"] = str(repo_path)
        called["proposal_id"] = proposal_id
        return {
            "dir": proposal_dir,
            "evidence": proposal_dir / "evidence.json",
            "topics": proposal_dir / "topics.json",
            "wiki": proposal_dir / "wiki.md",
        }

    monkeypatch.setattr("lib.topics.proposals.external_jobs.start_external_regenerate_run", fake_start)

    resp = flask_client.post(f"/api/repos/{name}/topics/proposals/run1/regenerate", json={})

    assert resp.status_code == 200
    assert called == {"repo_path": str(fake_git_repo), "proposal_id": "run1"}


def test_topics_proposal_feedback_threads_round_trip(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"scope": "all", "provider": "langchain"},
    )
    proposal_id = create_resp.get_json()["proposal"]["id"]
    proposal = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}").get_json()["proposal"]
    proposed_topic_id = proposal["topics"][0]["id"]

    create_thread_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/feedback-threads",
        json={
            "proposal_topic_id": proposed_topic_id,
            "anchor_kind": "topic_field",
            "anchor": {"topic_id": proposed_topic_id, "field": "intent"},
            "quoted_text": proposal["topics"][0]["intent"],
            "body": "Please tighten the intent before applying.",
        },
    )

    assert create_thread_resp.status_code == 200
    thread = create_thread_resp.get_json()["feedback_thread"]
    assert thread["revision_number"] == 1
    assert thread["proposal_topic_id"] == proposed_topic_id
    assert thread["comment_count"] == 1

    reply_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/feedback-threads/{thread['id']}/comments",
        json={"body": "Will address this in the next revision."},
    )

    assert reply_resp.status_code == 200
    replied = reply_resp.get_json()["feedback_thread"]
    assert replied["comment_count"] == 2
    assert replied["comments"][1]["body"] == "Will address this in the next revision."

    detail_resp = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.get_json()["feedback_threads"][0]["comment_count"] == 2

    workspace_resp = flask_client.get(
        f"/api/repos/{name}/topics/workspace/proposals",
        query_string={"proposal_id": proposal_id, "draft_topic_id": proposed_topic_id},
    )
    assert workspace_resp.status_code == 200
    workspace = workspace_resp.get_json()
    assert workspace["feedback_summary"]["thread_count"] == 1
    assert workspace["feedback_summary"]["selected_topic_thread_count"] == 1
    assert workspace["draft_topics"][0]["feedback_thread_count"] == 1
    assert workspace["feedback_threads"][0]["anchor"]["field"] == "intent"


def test_topics_proposal_review_state_endpoint(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"scope": "all", "provider": "langchain"},
    )
    proposal_id = create_resp.get_json()["proposal"]["id"]

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/review-state",
        json={"review_state": "ready_to_apply"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["proposal"]["status"] == "ready_to_apply"


def test_topics_proposal_delete_endpoint(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "proposal"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]

    delete_resp = flask_client.post(f"/api/repos/{name}/topics/proposals/{proposal_id}/delete", json={})

    assert delete_resp.status_code == 200
    assert delete_resp.get_json()["proposal"]["deleted"] is True
    assert not (fake_git_repo / ".regin/topics/proposals" / proposal_id).exists()


def test_topics_proposal_accept_endpoint(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "proposal"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]
    detail = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}").get_json()["proposal"]
    proposed_topic_id = detail["topics"][0]["id"]
    ready_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/review-state",
        json={"review_state": "ready_to_apply"},
    )
    assert ready_resp.status_code == 200

    accept_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{proposed_topic_id}/apply",
        json={"strategy": "create"},
    )

    assert accept_resp.status_code == 200
    body = accept_resp.get_json()
    assert body["ok"] is True
    assert body["applied_diff"]["topic_deltas"][0]["topic_id"] == proposed_topic_id
    topics_resp = flask_client.get(f"/api/repos/{name}/topics")
    assert any(topic["id"] == proposed_topic_id for topic in topics_resp.get_json()["topics"])

    # The proposal's wiki.md must land at .regin/topics/wiki/<id>.md.
    # /apply previously called apply_diff without wiki_pages, so the per-topic
    # wiki file was never written — accepting silently lost the agent's wiki.
    proposal_wiki = fake_git_repo / ".regin/topics/proposals" / proposal_id / "wiki.md"
    approved_wiki = fake_git_repo / ".regin/topics/wiki" / f"{proposed_topic_id}.md"
    assert approved_wiki.exists()
    assert approved_wiki.read_text() == proposal_wiki.read_text()


def test_topics_proposal_reapply_after_downgrade_actually_reapplies(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    """After downgrade, re-applying the source proposal must actually
    re-add the topic. Without the live-graph check on /apply's idempotency
    short-circuit, the stale snapshot from the first apply returns
    already_applied: true and the user's reapply silently does nothing.
    """
    import time
    from lib.topics.proposals import downgrade_topic_to_proposal
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "proposal"])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]
    detail = flask_client.get(f"/api/repos/{name}/topics/proposals/{proposal_id}").get_json()["proposal"]
    proposed_topic_id = detail["topics"][0]["id"]
    flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/review-state",
        json={"review_state": "ready_to_apply"},
    )
    flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{proposed_topic_id}/apply",
        json={"strategy": "create"},
    )

    # proposal_id is derived from utc_now() truncated to the second, so a
    # same-second downgrade would collide with the source proposal's dir.
    time.sleep(1.1)
    downgrade_topic_to_proposal(fake_git_repo, proposed_topic_id)
    assert proposed_topic_id not in load_authoritative_graph(fake_git_repo).get("topics", {})

    flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/review-state",
        json={"review_state": "ready_to_apply"},
    )
    reapply = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{proposed_topic_id}/apply",
        json={"strategy": "create"},
    )
    body = reapply.get_json()
    assert reapply.status_code == 200
    assert body["ok"] is True
    assert body.get("already_applied") is not True
    assert proposed_topic_id in load_authoritative_graph(fake_git_repo).get("topics", {})


def test_topics_delete_endpoint_removes_topic_and_prunes_edges(flask_client, tmp_db, fake_git_repo):
    """POST /topics/<id>/delete hard-removes an approved topic and prunes
    inbound edges, leaving it gone from the effective graph."""
    from lib.topics import load_graph, load_graph_merged, save_graph
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    g = load_graph(fake_git_repo)
    g["topics"]["doomed"] = {
        "label": "Doomed", "aliases": [], "intent": "x", "status": "active",
        "refs": [], "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
    }
    g["topics"]["keeper"] = {
        "label": "Keeper", "aliases": [], "intent": "y", "status": "active",
        "refs": [], "edges": [{"target": "doomed", "type": "related"}],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, g)
    load_authoritative_graph(fake_git_repo)  # seed snapshot

    resp = flask_client.post(f"/api/repos/{name}/topics/doomed/delete", json={})

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["ok"] is True
    assert body["pruned_edges"] == 1
    merged = load_graph_merged(fake_git_repo)
    assert "doomed" not in merged["topics"]
    assert merged["topics"]["keeper"]["edges"] == []


def test_topics_import_endpoint_seeds_snapshot(flask_client, tmp_db, fake_git_repo):
    """The Sync-from-git endpoint seeds a teammate's git-shipped topics into
    the local snapshot (and best-effort rebuilds the wiki search index)."""
    from lib.topics import load_graph, save_graph
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    g = load_graph(fake_git_repo)
    g["topics"]["shared"] = {
        "label": "Shared", "aliases": [], "intent": "from a teammate", "status": "active",
        "refs": [], "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, g)

    res = flask_client.post(f"/api/repos/{name}/topics/import", json={}).get_json()

    assert res["ok"] is True
    assert res["state"] == "seeded"
    assert res["topic_count"] == 1
    assert "wiki_index" in res  # reindex ran (or was best-effort-skipped)


def test_topics_proposal_accept_endpoint_drops_unapproved_edges(stub_proposal_provider, flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo, seeds=True)
    create_resp = flask_client.post(f"/api/repos/{name}/topics/proposals", json={"scope": "all"})
    proposal_id = create_resp.get_json()["proposal"]["id"]
    from lib.topics.proposals import load_proposal, save_proposal
    proposal = load_proposal(fake_git_repo, proposal_id)
    proposal["topics"] = [{
        "id": "custom-rule-engines",
        "label": "Custom Rule Engines",
        "aliases": [],
        "intent": "Rule engine proposal.",
        "status": "active",
        "refs": [],
        "edges": [
            {"rel": "related", "to": "overview"},
            {"rel": "used-by", "to": "hook-manager"},
            {"rel": "related", "to": "missing-topic"},
        ],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
        "evidence_paths": [],
    }]
    proposal["status"] = "ready_to_apply"
    save_proposal(fake_git_repo, proposal_id, proposal)

    accept_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/custom-rule-engines/apply",
        json={"strategy": "create"},
    )

    assert accept_resp.status_code == 200
    body = accept_resp.get_json()
    assert body["ok"] is True
    # The /apply default has prune_orphan_edges=True so the two
    # missing-target edges drop. Only "overview" remains (it's a real
    # topic seeded by the proposal flow).
    delta = body["applied_diff"]["topic_deltas"][0]
    assert delta["after"]["edges"] == [{"type": "related", "target": "overview"}]


def test_topics_proposal_merge_and_ignore_endpoints(flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    from lib.topics import load_graph, save_graph
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": [],
        "intent": "Web API.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    resp = flask_client.post(f"/api/repos/{name}/topics/wiki", json={})

    assert resp.status_code == 200
    body = resp.get_json()
    # Post-Phase-E: generate_wiki only writes index.md — per-topic
    # files are owned by accept-from-proposal.
    assert body["count"] == 1
    assert (fake_git_repo / ".regin/topics/wiki/index.md").exists()


def test_topics_downgrade_endpoint_moves_approved_topic_to_proposal(flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("service")
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    from lib.topics import load_graph, save_graph
    graph = load_graph(fake_git_repo)
    graph["topics"]["service"] = {
        "label": "Service",
        "aliases": ["svc"],
        "intent": "Service API.",
        "status": "active",
        "refs": [{"path": "service/api.py", "role": "implementation"}],
        "edges": [],
        "commands": [],
        "include_globs": ["service/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    resp = flask_client.post(f"/api/repos/{name}/topics/service/downgrade", json={})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["topic"]["id"] == "service"
    topics_resp = flask_client.get(f"/api/repos/{name}/topics")
    assert not any(topic["id"] == "service" for topic in topics_resp.get_json()["topics"])
    proposal_dir = fake_git_repo / ".regin/topics/proposals" / body["id"]
    from lib.topics.proposals import load_proposal
    assert load_proposal(fake_git_repo, body["id"]) is not None
    assert (proposal_dir / "wiki.md").exists()


def test_topic_detail_includes_wiki_content_preview(flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    from lib.topics import load_graph, save_graph
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": [],
        "intent": "Web API.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    resp = flask_client.get(f"/api/repos/{name}/topics/web")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["wiki_content"].startswith("# Web")
    assert "## References" in body["wiki_content"]


def test_topics_workspace_wiki_and_summary_return_selected_topic_and_counts(flask_client, tmp_db, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    from lib.topics import load_graph, save_graph
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": ["http"],
        "intent": "Web API.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    summary_resp = flask_client.get(f"/api/repos/{name}/topics/workspace/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.get_json()
    assert summary["approved_topic_count"] >= 1

    resp = flask_client.get(f"/api/repos/{name}/topics/workspace/wiki", query_string={"topic_id": "web"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["selected_topic_id"] == "web"
    assert body["selected_topic"]["label"] == "Web"
    assert body["table"][0]["broken_ref_count"] == 0

    # Bookmark / stale URL fall-through: a `?topic_id=` for a topic
    # that was deleted after the user navigated to it must NOT 500.
    # The payload silently falls back to the first remaining topic.
    stale = flask_client.get(
        f"/api/repos/{name}/topics/workspace/wiki",
        query_string={"topic_id": "deleted-long-ago"},
    )
    assert stale.status_code == 200
    body = stale.get_json()
    assert body["selected_topic_id"] == "web"  # fell back to the first row
