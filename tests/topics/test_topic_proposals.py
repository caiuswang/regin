"""Tests for reviewable topic proposal artifacts."""

from __future__ import annotations

import json
import subprocess

import pytest

from lib.orm.engine import get_connection
from lib.topics.proposal_external import write_status
from lib.topics.proposals import (
    accept_proposed_topic,
    add_proposal_feedback_comment,
    create_proposal_run,
    create_proposal_feedback_thread,
    delete_proposal_run,
    downgrade_topic_to_proposal,
    ignore_proposed_topic,
    list_proposal_revisions,
    list_proposal_runs,
    load_proposal,
    load_proposal_status,
    merge_proposed_topic,
    regenerate_proposal_run,
    save_proposal,
    set_proposal_review_state,
    update_proposed_topic,
)
from lib.topics.proposal_drafting import (
    format_review_feedback_for_prompt,
    validate_proposal,
)
from lib.topics import TopicGraphError, bootstrap, load_graph, load_graph_merged, save_graph, utc_now


def test_create_proposal_run_writes_artifacts(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)

    paths = create_proposal_run(fake_git_repo, run_id="run1")

    assert paths["wiki"].exists()
    proposal = load_proposal(fake_git_repo, "run1")
    assert proposal is not None
    assert proposal["topics"][0]["id"] == "stub-topic"
    assert list_proposal_runs(fake_git_repo)[0]["id"] == "run1"


def test_create_proposal_run_does_not_mutate_approved_topic_graph(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo, seeds=True)
    before = (fake_git_repo / ".regin/topics/topic.json").read_text()

    create_proposal_run(fake_git_repo, run_id="run1")

    after = (fake_git_repo / ".regin/topics/topic.json").read_text()
    assert after == before


def test_create_external_agent_proposal_run_writes_status_and_trace(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)

    script = tmp_path / "agent.py"
    script.write_text(
        "import json, os\n"
        "payload = {'topics': [{'id': 'service', 'label': 'Service', 'aliases': [], "
        "'intent': 'Curated context for Service.', 'status': 'active', "
        "'refs': [{'path': 'service/api.py', 'role': 'implementation'}], "
        "'edges': [], 'commands': [], 'include_globs': ['service/**'], "
        "'exclude_globs': [], "
        "'evidence_paths': ['service/api.py', 'service/model.py']}], "
        "'notes': [], 'wiki': '# Service\\n'}\n"
        "open(os.environ['REGIN_TOPIC_PROPOSAL_OUTPUT'], 'w').write(json.dumps(payload))\n"
    )
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    paths = create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    proposal = load_proposal(fake_git_repo, "run1")
    status = load_proposal_status(fake_git_repo, "run1")
    raw_output = fake_git_repo / ".regin/topics/proposals/run1/agent-output.json"
    temp_output = fake_git_repo / ".regin/topics/proposals/run1/.tmp/agent-output.json"
    assert proposal["provider"] == "external-agent"
    assert proposal["topics"][0]["id"] == "service"
    assert status["state"] == "completed"
    assert status["trace_id"] == "topic-proposal-run1"
    assert raw_output.exists()
    assert temp_output.exists()
    assert json.loads(raw_output.read_text())["topics"][0]["id"] == "service"
    with get_connection() as conn:
        session_row = conn.execute(
            "SELECT title, title_source, status, agent_type FROM sessions WHERE trace_id = ?",
            ("topic-proposal-run1",),
        ).fetchone()
        span_names = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM session_spans WHERE trace_id = ? ORDER BY id",
                ("topic-proposal-run1",),
            ).fetchall()
        ]
    assert session_row is not None
    assert session_row["title"] == "Topic proposal pipeline (evidence → draft → review)"
    assert session_row["title_source"] == "claude_ai_title"
    assert session_row["status"] == "ended"
    assert session_row["agent_type"] == "topic-proposal-agent"
    assert "session.start" in span_names
    assert "session.title" in span_names
    assert "session.end" in span_names


def test_external_agent_run_rejects_stale_temp_output_from_prior_attempt(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)

    stale_payload = {
        "topics": [{
            "id": "service",
            "label": "Service",
            "aliases": [],
            "intent": "Curated context for Service.",
            "status": "active",
            "refs": [{"path": "service/api.py", "role": "implementation"}],
            "edges": [],
            "commands": [],
            "include_globs": ["service/**"],
            "exclude_globs": [],
            "evidence_paths": ["service/api.py"],
        }],
        "notes": [],
        "wiki": "# Service\n",
    }
    temp_output = fake_git_repo / ".regin/topics/proposals/run1/.tmp/agent-output.json"
    temp_output.parent.mkdir(parents=True, exist_ok=True)
    temp_output.write_text(json.dumps(stale_payload))

    script = tmp_path / "agent.py"
    script.write_text("print()\n")
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    with pytest.raises(TopicGraphError, match="external agent output invalid"):
        create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    assert not temp_output.exists()


def test_write_status_clears_old_error_when_run_completes(fake_git_repo, tmp_db):
    bootstrap(fake_git_repo)
    out_dir = fake_git_repo / ".regin/topics/proposals/run1"
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now()
    write_status(out_dir, {
        "state": "failed",
        "trace_id": "topic-proposal-run1",
        "agent": "fake",
        "started_at": started_at,
        "completed_at": utc_now(),
        "error": "boom",
        "error_detail": "detail",
    })
    failed = load_proposal_status(fake_git_repo, "run1")
    assert failed["error"] == "boom"
    assert failed["error_detail"] == "detail"

    write_status(out_dir, {
        "state": "completed",
        "trace_id": "topic-proposal-run1",
        "agent": "fake",
        "started_at": started_at,
        "completed_at": utc_now(),
        "error": None,
        "error_detail": None,
    })
    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "completed"
    assert status["error"] is None
    assert status["error_detail"] is None


def test_external_agent_permission_prompt_fails_clearly(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    bootstrap(fake_git_repo)
    script = tmp_path / "agent.py"
    script.write_text("print('Do you want to allow this command?')\n")
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    with pytest.raises(TopicGraphError, match="requested interactive permission"):
        create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "waiting_for_permission"
    assert not (fake_git_repo / ".regin/topics/proposals/run1/topics.json").exists()


def test_external_agent_permission_block_text_fails_even_with_json(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    bootstrap(fake_git_repo)
    script = tmp_path / "agent.py"
    script.write_text(
        "print('I drafted the proposal payload, but the artifact file was not written because the write permission prompt has not been approved yet.')\n"
        "print('```json')\n"
        "print('{\"topics\": []}')\n"
        "print('```')\n"
    )
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    with pytest.raises(TopicGraphError, match="requested interactive permission"):
        create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "waiting_for_permission"
    assert not (fake_git_repo / ".regin/topics/proposals/run1/topics.json").exists()


def test_external_agent_nonzero_exit_includes_output_in_status(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    bootstrap(fake_git_repo)
    script = tmp_path / "agent.py"
    script.write_text(
        "print('first line')\n"
        "print('API Error: 503 No available channel')\n"
        "print('final stdout line with useful detail')\n"
        "raise SystemExit(1)\n"
    )
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    with pytest.raises(TopicGraphError, match="No available channel"):
        create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    status = load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "failed"
    assert "external agent exited with code 1" in status["error"]
    assert "API Error: 503 No available channel" in status["error"]
    assert status["error_detail"]
    assert "API Error: 503 No available channel" in status["error_detail"]
    assert status["stdout_tail"]
    assert "final stdout line with useful detail" in status["stdout_tail"]
    assert status["stderr_tail"] is None


def test_external_agent_failure_status_bounds_detailed_output(monkeypatch, fake_git_repo, tmp_path, tmp_db):
    bootstrap(fake_git_repo)
    script = tmp_path / "agent.py"
    script.write_text(
        "import sys\n"
        f"print({'A' * 2500!r})\n"
        f"sys.stderr.write({'B' * 2500!r})\n"
        "raise SystemExit(1)\n"
    )
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"fake": type("Cfg", (), {"command": "python", "args": [str(script)], "timeout_seconds": 30, "cwd": None})()},
    )

    with pytest.raises(TopicGraphError):
        create_proposal_run(fake_git_repo, run_id="run1", agent="fake")

    status = load_proposal_status(fake_git_repo, "run1")
    assert len(status["error"]) <= 600
    assert status["error_detail"].startswith("...")
    assert len(status["error_detail"]) == 4000
    assert status["stdout_tail"].startswith("...")
    assert len(status["stdout_tail"]) == 2000
    assert status["stderr_tail"].startswith("...")
    assert len(status["stderr_tail"]) == 2000


def test_normalise_agent_payload_rejects_missing_label(fake_git_repo):
    from lib.topics.proposal_external import _normalise_agent_payload

    payload = {"version": 1, "topics": [{"id": "bad"}], "wiki": "draft"}
    with pytest.raises(ValueError, match="label is required"):
        _normalise_agent_payload(fake_git_repo, payload)


def test_normalise_agent_payload_rejects_blank_wiki(fake_git_repo):
    from lib.topics.proposal_external import _normalise_agent_payload

    payload = {
        "version": 1,
        "topics": [{
            "id": "service",
            "label": "Service",
            "aliases": [],
            "intent": "Curated context for Service.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
        }],
        "wiki": "   ",
    }
    with pytest.raises(ValueError, match="empty wiki"):
        _normalise_agent_payload(fake_git_repo, payload)


def test_regenerate_proposal_run_reuses_same_run_and_passes_prior_draft(stub_proposal_provider, monkeypatch, fake_git_repo):
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    original_wiki = paths["wiki"].read_text()
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["provider"] = "stub"
    proposal["metadata"] = {"complexity": "standard", "agent": "kept-agent"}
    # `kept-agent` is still a configured agent, so regenerate reuses it verbatim
    # (the fallback only kicks in when the prior agent is gone).
    from lib.settings import TopicProposalExternalAgent, settings
    monkeypatch.setattr(
        settings,
        "topic_proposal_external_agents",
        {"kept-agent": TopicProposalExternalAgent(command="kept-agent")},
    )
    proposal["topics"] = [{
        "id": "service",
        "label": "Service Layer",
        "aliases": ["svc"],
        "intent": "Service.",
        "status": "active",
        "refs": [],
        "edges": [],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
        "evidence_paths": [],
    }]
    save_proposal(fake_git_repo, "run1", proposal)
    paths["wiki"].write_text("# Previous draft wiki\n")
    thread = create_proposal_feedback_thread(
        fake_git_repo,
        "run1",
        proposal_topic_id="service",
        anchor_kind="topic_field",
        anchor={"topic_id": "service", "field": "intent"},
        quoted_text="Original intent.",
        body="Please tighten the intent before applying.",
    )
    add_proposal_feedback_comment(
        fake_git_repo,
        "run1",
        thread["id"],
        body="Focus on concrete repo responsibilities.",
    )

    captured: dict[str, object] = {}

    def fake_draft(*, repo, out_dir, proposal_id, topic_request=None, scope="all", agent=None, prior_draft=None, prompt_templates=None):
        del prompt_templates, topic_request, scope, repo, out_dir
        captured["proposal_id"] = proposal_id
        captured["agent"] = agent
        captured["prior_draft"] = prior_draft
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

    result = regenerate_proposal_run(fake_git_repo, "run1")

    assert result["dir"].name == "run1"
    assert captured["proposal_id"] == "run1"
    assert captured["agent"] == "kept-agent"
    # Regenerate hands the drafter the round-tripped PRIOR proposal
    # as `prior_draft`. The drafter sees what was in the ORM right
    # before this run started — the planted "service" topic — not
    # the freshly-regenerated state. Compare the prior topic ids
    # against what we planted.
    assert captured["prior_draft"]["wiki"] == "# Previous draft wiki\n"
    prior_topic_ids = [t["id"] for t in captured["prior_draft"]["proposal"]["topics"]]
    assert prior_topic_ids == ["service"]
    assert [t["anchor_kind"] for t in captured["prior_draft"]["feedback_threads"]] == ["topic_field"]
    assert captured["prior_draft"]["feedback_threads"][0]["comments"][0]["body"] == "Please tighten the intent before applying."
    assert captured["prior_draft"]["feedback_threads"][0]["comments"][1]["body"] == "Focus on concrete repo responsibilities."
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["topics"][0]["id"] == "service-regenerated"
    assert saved["revision"]["revision_number"] == 2
    revisions = list_proposal_revisions(fake_git_repo, "run1")
    assert [revision["revision_number"] for revision in revisions] == [2, 1]
    assert revisions[0]["kind"] == "regenerated"
    assert paths["wiki"].read_text() == "# Refreshed wiki"


def _plant_regenerable_proposal(repo, agent):
    """A minimal completed run whose persisted metadata carries `agent`, ready
    to regenerate."""
    create_proposal_run(repo, run_id="run1")
    proposal = load_proposal(repo, "run1")
    proposal["metadata"] = {"agent": agent}
    proposal["topics"] = [{
        "id": "svc", "label": "Svc", "aliases": [], "intent": "S.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [], "evidence_paths": [],
    }]
    save_proposal(repo, "run1", proposal)


def _capture_regenerate_agent(monkeypatch):
    """Monkeypatch the drafter to capture the `agent` regenerate hands it."""
    captured: dict[str, object] = {}

    def fake_draft(*, repo, out_dir, proposal_id, topic_request=None, scope="all", agent=None, prior_draft=None, prompt_templates=None):
        del repo, out_dir, proposal_id, topic_request, scope, prompt_templates
        captured["agent"] = agent
        refreshed = dict(prior_draft["proposal"])
        return refreshed, "# Refreshed wiki\n"

    monkeypatch.setattr("lib.topics.proposals.external_jobs._draft_proposal", fake_draft)
    return captured


def test_regenerate_drops_prior_agent_when_no_longer_configured(stub_proposal_provider, monkeypatch, fake_git_repo):
    """The prior run's agent may have been renamed/removed since. Regenerate must
    drop the dead id (→ None) so `_resolve_agent_config`'s fallback chain picks
    the current related agent instead of raising `unknown external topic proposal
    agent`."""
    from lib.settings import TopicProposalExternalAgent, settings

    bootstrap(fake_git_repo)
    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="claude")},
    )
    _plant_regenerable_proposal(fake_git_repo, "removed-agent")
    captured = _capture_regenerate_agent(monkeypatch)

    regenerate_proposal_run(fake_git_repo, "run1")

    # `removed-agent` is gone → dropped to None so the drafter resolves the
    # live fallback (surface binding → default), not the dead id.
    assert captured["agent"] is None


def test_regenerate_keeps_prior_agent_when_still_configured(stub_proposal_provider, monkeypatch, fake_git_repo):
    """Contrast to the drop case: a still-configured prior agent is reused
    verbatim — the fallback is only for stale ids."""
    from lib.settings import TopicProposalExternalAgent, settings

    bootstrap(fake_git_repo)
    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"codex": TopicProposalExternalAgent(command="codex")},
    )
    _plant_regenerable_proposal(fake_git_repo, "codex")
    captured = _capture_regenerate_agent(monkeypatch)

    regenerate_proposal_run(fake_git_repo, "run1")

    assert captured["agent"] == "codex"


def test_regenerate_agent_or_fallback_unit(monkeypatch):
    """Unit: the gate keeps configured ids, drops stale/None ones."""
    from lib.settings import TopicProposalExternalAgent, settings
    from lib.topics.proposals.external_jobs import _regenerate_agent_or_fallback

    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="claude")},
    )
    assert _regenerate_agent_or_fallback("claude") == "claude"
    assert _regenerate_agent_or_fallback("removed-agent") is None
    assert _regenerate_agent_or_fallback(None) is None


def test_resolve_agent_config_none_falls_back_to_default(monkeypatch):
    """The dropped-to-None agent resolves to the configured default without
    raising — the mechanism the regenerate fallback relies on."""
    from lib.settings import TopicProposalExternalAgent, settings
    from lib.topics.proposal_external import _resolve_agent_config

    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="claude")},
    )
    agent, config = _resolve_agent_config(None)
    assert agent == "claude"
    assert config.command == "claude"


def test_resolve_agent_config_still_rejects_explicit_unknown_agent(monkeypatch):
    """The regenerate fallback is scoped to inherited ids; an explicit per-run
    pick of an unknown agent (a fresh-run picker) must still raise, not silently
    swap."""
    from lib.settings import TopicProposalExternalAgent, settings
    from lib.topics.proposal_external import _resolve_agent_config

    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="claude")},
    )
    with pytest.raises(ValueError, match="unknown external topic proposal agent"):
        _resolve_agent_config("bogus")


def test_regenerate_clears_stale_accept_markers_on_new_revision(stub_proposal_provider, monkeypatch, fake_git_repo):
    """Regression: a regenerated topic must not inherit the prior revision's accept marker.

    If a topic was accepted, then the proposal regenerated, the new draft
    content is different from what was accepted. Leaving review_status=
    'accepted' on the new revision hid Edit/Apply/Ignore in the UI,
    stranding the user on an unactionable draft. Some providers echoed the
    prior topic dict back verbatim, so we defend at the regenerate layer.
    """
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")

    def echo_prior_draft(*, repo, out_dir, proposal_id, topic_request=None, scope="all", agent=None, prior_draft=None, prompt_templates=None):
        # Simulate an agent that copies the prior topic dict (including
        # stale accept markers) into its output instead of stripping them.
        refreshed = dict(prior_draft["proposal"])
        refreshed["topics"] = [{
            "id": "service",
            "label": "Service",
            "intent": "Refreshed.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
            "review_status": "accepted",
            "accepted_topic": "service",
            "accepted_at": "2026-05-20T02:59:20Z",
        }]
        return refreshed, "# Refreshed wiki\n"

    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"][0]["review_status"] = "accepted"
    proposal["topics"][0]["accepted_topic"] = proposal["topics"][0]["id"]
    proposal["topics"][0]["accepted_at"] = "2026-05-20T02:59:20Z"
    save_proposal(fake_git_repo, "run1", proposal)

    monkeypatch.setattr("lib.topics.proposals.external_jobs._draft_proposal", echo_prior_draft)
    regenerate_proposal_run(fake_git_repo, "run1")

    saved = load_proposal(fake_git_repo, "run1")
    topic = saved["topics"][0]
    assert topic.get("review_status") in (None, "")
    assert topic.get("accepted_topic") in (None, "")
    assert topic.get("accepted_at") in (None, "")


def test_regenerate_proposal_run_preserves_existing_artifacts_on_failure(stub_proposal_provider, monkeypatch, fake_git_repo):
    from lib.topics.proposal_external import load_status

    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    original_proposal = load_proposal(fake_git_repo, "run1")
    original_wiki = paths["wiki"].read_text()

    def fail_draft(**kwargs):
        raise TopicGraphError("boom")

    monkeypatch.setattr("lib.topics.proposals.external_jobs._draft_proposal", fail_draft)

    # Regenerate runs the agent in the background; a failure is captured in
    # the run's status file, and the prior draft/artifacts are untouched.
    regenerate_proposal_run(fake_git_repo, "run1")

    # The prior draft's topics + wiki are untouched (no new revision landed).
    assert load_proposal(fake_git_repo, "run1")["topics"] == original_proposal["topics"]
    assert paths["wiki"].read_text() == original_wiki
    status = load_status(paths["dir"])
    assert status["state"] == "failed"
    assert "boom" in (status["error"] or "")



def test_load_proposal_prefers_orm_over_stale_disk_topics_json(stub_proposal_provider, fake_git_repo):
    """Regression: a stale disk topics.json must not shadow ORM writes.

    Before this fix, `load_proposal` was disk-first. Once `_write_proposal_artifacts`
    stopped writing topics.json (commit a4e31b1), any pre-cutover proposal kept
    its frozen disk file as the authoritative reader — every regenerate or
    mark-ready ORM write was silently invisible on the next read. This caused
    the "Mark ready button click has no effect" report.
    """
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")

    # Plant a pre-cutover disk file that disagrees with the ORM truth.
    topics_path = fake_git_repo / ".regin/topics/proposals/run1/topics.json"
    topics_path.write_text(json.dumps({
        "version": 1,
        "status": "draft",
        "topics": [],
        "provider": "stub",
    }))

    set_proposal_review_state(fake_git_repo, "run1", "ready_to_apply")

    # ORM is the source of truth; disk file is irrelevant.
    assert load_proposal(fake_git_repo, "run1")["status"] == "ready_to_apply"


def test_delete_proposal_run_removes_artifacts(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")

    result = delete_proposal_run(fake_git_repo, "run1")

    assert result == {"id": "run1", "deleted": True}
    assert not paths["dir"].exists()


def test_delete_proposal_run_rejects_path_traversal(fake_git_repo):
    bootstrap(fake_git_repo)

    with pytest.raises(TopicGraphError, match="invalid proposal id"):
        delete_proposal_run(fake_git_repo, "../topic.json")


def test_validate_proposal_reports_missing_fields():
    errors = validate_proposal({"version": 1, "topics": [{"id": "x"}]})

    assert "topics[0].label is required" in errors
    assert "topics[0].refs must be a list" not in errors


def test_format_review_feedback_empty_returns_blank():
    assert format_review_feedback_for_prompt(None) == ""
    assert format_review_feedback_for_prompt([]) == ""


def test_format_review_feedback_full_thread():
    threads = [
        {
            "proposal_topic_id": "t1",
            "anchor_kind": "topic_field",
            "anchor": {"field": "label"},
            "quoted_text": "  old label  ",
            "comments": [
                {"author_kind": "human", "body": "  please rename  "},
                {"body": "no author here"},
            ],
        }
    ]
    assert format_review_feedback_for_prompt(threads) == "\n".join(
        [
            "Review feedback to address in this revision:",
            "1. topic `t1`, field `label`",
            '   Quoted text: "old label"',
            "   - human: please rename",
            "   - reviewer: no author here",
        ]
    )


def test_format_review_feedback_anchor_kinds_and_fallbacks():
    threads = [
        {"anchor_kind": "proposal_summary"},
        {"anchor_kind": "wiki_range"},
        {"anchor_kind": "general"},
        {"anchor_kind": "unknown_kind"},
        {"anchor_kind": "topic_field", "anchor": {}},
        {"proposal_topic_id": "", "anchor_kind": "topic_field"},
        {"proposal_topic_id": 123, "anchor_kind": "general"},
    ]
    assert format_review_feedback_for_prompt(threads) == "\n".join(
        [
            "Review feedback to address in this revision:",
            "1. proposal summary",
            "2. wiki content",
            "3. general review",
            "4. general review",
            "5. general review",
            "6. general review",
            "7. general review",
        ]
    )


def test_format_review_feedback_skips_blank_and_nonstring_comments():
    threads = [
        {
            "anchor_kind": "general",
            "quoted_text": "   ",
            "comments": [
                {"body": "   "},
                {"body": None},
                {"body": 42},
                {"author_kind": "human", "body": "keep me"},
            ],
        }
    ]
    assert format_review_feedback_for_prompt(threads) == "\n".join(
        [
            "Review feedback to address in this revision:",
            "1. general review",
            "   - human: keep me",
        ]
    )


def test_accept_proposed_topic_promotes_one_topic(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
            "id": "service",
            "label": "Service",
            "aliases": ["svc"],
            "intent": "Curated context for Service.",
            "status": "active",
            "refs": [{"path": "service/api.py", "role": "implementation"}],
            "edges": [],
            "commands": [],
            "include_globs": ["service/**"],
            "exclude_globs": [],
            "evidence_paths": ["service/api.py"],
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    approved = accept_proposed_topic(fake_git_repo, "run1", "service")

    graph = load_graph_merged(fake_git_repo)
    updated_proposal = load_proposal(fake_git_repo, "run1")
    assert approved["id"] == "service"
    assert graph["topics"]["service"]["aliases"] == ["svc"]
    assert updated_proposal["topics"][0]["review_status"] == "accepted"


def test_accept_proposed_topic_keeps_valid_drops_invalid_ref_roles(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "lib").mkdir()
    (fake_git_repo / "lib" / "settings.py").write_text("VALUE = 1\n")
    (fake_git_repo / "tests").mkdir()
    (fake_git_repo / "tests" / "test_settings.py").write_text("def test_value(): pass\n")
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
            "id": "settings-flow",
            "label": "Settings Flow",
            "aliases": [],
            "intent": "Settings proposal.",
            "status": "active",
            "refs": [
                {"path": "lib/settings.py", "role": "config"},
                {"path": "tests/test_settings.py", "role": "tests"},
            ],
            "edges": [],
            "commands": [],
            "include_globs": ["lib/settings.py", "tests/test_settings.py"],
            "exclude_globs": [],
            "evidence_paths": ["lib/settings.py", "tests/test_settings.py"],
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    accept_proposed_topic(fake_git_repo, "run1", "settings-flow")

    graph = load_graph_merged(fake_git_repo)
    refs = {ref["path"]: ref for ref in graph["topics"]["settings-flow"]["refs"]}
    # A valid role survives; an unknown role is dropped, not re-inferred.
    assert refs["lib/settings.py"]["role"] == "config"
    assert "role" not in refs["tests/test_settings.py"]


def test_accept_proposed_topic_drops_unapproved_edges(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo, seeds=True)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
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
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    accepted = accept_proposed_topic(fake_git_repo, "run1", "custom-rule-engines")

    assert accepted["edges"] == [{"type": "related", "target": "overview"}]
    graph = load_graph_merged(fake_git_repo)
    assert graph["topics"]["custom-rule-engines"]["edges"] == [{"type": "related", "target": "overview"}]


def test_accept_proposed_topic_rejects_duplicate_topic_id(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
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
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    updated = update_proposed_topic(fake_git_repo, "run1", "service", {
        "label": "Service Layer",
        "aliases": ["svc"],
        "include_globs": ["service/**"],
    })

    saved = load_proposal(fake_git_repo, "run1")
    assert updated["label"] == "Service Layer"
    assert saved["topics"][0]["aliases"] == ["svc"]


def test_update_proposed_topic_rejects_invalid_patch(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
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
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    with pytest.raises(TopicGraphError, match="aliases must be a list"):
        update_proposed_topic(fake_git_repo, "run1", "service", {"aliases": "svc"})


def test_merge_proposed_topic_into_existing_topic(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("web")
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("service")
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": ["api"],
        "intent": "Web.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
            "id": "service",
            "label": "Service",
            "aliases": ["svc"],
            "intent": "Service.",
            "status": "active",
            "refs": [{"path": "service/api.py", "role": "implementation"}],
            "edges": [],
            "commands": ["pytest tests/test_service.py"],
            "include_globs": ["service/**"],
            "exclude_globs": ["service/generated/**"],
            "evidence_paths": ["service/api.py"],
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    merged = merge_proposed_topic(fake_git_repo, "run1", "service", "web")

    saved_graph = load_graph_merged(fake_git_repo)
    saved_proposal = load_proposal(fake_git_repo, "run1")
    assert merged["id"] == "web"
    assert any(ref["path"] == "service/api.py" for ref in saved_graph["topics"]["web"]["refs"])
    assert "svc" in saved_graph["topics"]["web"]["aliases"]
    assert saved_proposal["topics"][0]["review_status"] == "merged"


def test_merge_proposed_topic_drops_invalid_ref_roles(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("web")
    (fake_git_repo / "tests").mkdir()
    (fake_git_repo / "tests" / "test_web.py").write_text("def test_web(): pass\n")
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": [],
        "intent": "Web.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
            "id": "web-tests",
            "label": "Web Tests",
            "aliases": [],
            "intent": "Web tests.",
            "status": "active",
            "refs": [{"path": "tests/test_web.py", "role": "tests"}],
            "edges": [],
            "commands": [],
            "include_globs": ["tests/test_web.py"],
            "exclude_globs": [],
            "evidence_paths": ["tests/test_web.py"],
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    merge_proposed_topic(fake_git_repo, "run1", "web-tests", "web")

    graph = load_graph_merged(fake_git_repo)
    refs = {ref["path"]: ref for ref in graph["topics"]["web"]["refs"]}
    # The target's valid role is preserved; the merged-in unknown role is dropped.
    assert refs["web/app.py"]["role"] == "entrypoint"
    assert "role" not in refs["tests/test_web.py"]


def test_ignore_proposed_topic_marks_review_status(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    proposal["topics"] = [
        {
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
        }
    ]
    save_proposal(fake_git_repo, "run1", proposal)

    ignored = ignore_proposed_topic(fake_git_repo, "run1", "service")

    saved = load_proposal(fake_git_repo, "run1")
    assert ignored["review_status"] == "ignored"
    assert saved["topics"][0]["review_status"] == "ignored"


def test_downgrade_clears_stale_accepted_marker_on_source_proposal(stub_proposal_provider, fake_git_repo):
    """After downgrade, the source proposal's topic must be re-acceptable:
    the `review_status='accepted'` marker (set by accept_proposed_topic)
    is cleared and the run flips to `changes_requested` so the UI shows
    the topic as actionable again.

    Under the merge-into-origin design, the downgrade appends a new
    revision to the proposal that first applied the topic. The latest
    revision now carries the fresh draft snapshot (`pending`), and the
    run-level status follows — `applied` is no longer truthful once a
    new revision is sitting in the user's queue.
    """
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("service")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])
    bootstrap(fake_git_repo)
    paths = create_proposal_run(fake_git_repo, run_id="src-run")
    proposal = load_proposal(fake_git_repo, "src-run")
    proposed_topic_id = proposal["topics"][0]["id"]
    accept_proposed_topic(fake_git_repo, "src-run", proposed_topic_id)

    before = load_proposal(fake_git_repo, "src-run")
    accepted = next(t for t in before["topics"] if t["id"] == proposed_topic_id)
    assert accepted["review_status"] == "accepted"

    downgrade_topic_to_proposal(fake_git_repo, proposed_topic_id)

    after = load_proposal(fake_git_repo, "src-run")
    cleared = next(t for t in after["topics"] if t["id"] == proposed_topic_id)
    assert cleared.get("review_status") in (None, "pending")
    assert cleared.get("accepted_topic") is None
    assert cleared.get("accepted_at") is None
    assert after.get("status") == "changes_requested"


def test_downgrade_topic_to_proposal_moves_topic_back_to_draft(stub_proposal_provider, fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("service")
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["service"] = {
        "label": "Service",
        "aliases": ["svc"],
        "intent": "Service.",
        "status": "active",
        "refs": [{"path": "service/api.py", "role": "implementation"}],
        "edges": [],
        "commands": ["pytest tests/test_service.py"],
        "include_globs": ["service/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "service")

    updated_graph = load_graph_merged(fake_git_repo)
    assert "service" not in updated_graph["topics"]
    assert result["topic"]["id"] == "service"
    wiki_path = fake_git_repo / ".regin/topics/proposals" / result["id"] / "wiki.md"
    downgraded = load_proposal(fake_git_repo, result["id"])
    assert downgraded is not None
    assert wiki_path.exists()
    assert downgraded["provider"] == "approved-topic-downgrade"


def test_downgrade_collision_returns_helpful_error_not_500(stub_proposal_provider, fake_git_repo, monkeypatch):
    """Two downgrades within the same wall-clock second derive the same
    proposal_id. Without the FileExistsError catch this surfaces as a
    raw 500 in the web layer; with it, the second call raises a
    TopicGraphError so the Flask handler returns 400 with a clear hint."""
    bootstrap(fake_git_repo, seeds=True)

    # Pre-create the proposal_dir at the timestamp the next downgrade
    # will derive — emulates a same-second concurrent attempt.
    from lib.topics import topic_dir
    from lib.topics.core import utc_now
    from lib.topics.proposals import downgrade as downgrade_mod
    fixed_ts = utc_now()
    monkeypatch.setattr(downgrade_mod, "utc_now", lambda: fixed_ts)
    expected_id = fixed_ts.replace(":", "").replace("-", "")
    (topic_dir(fake_git_repo) / "proposals" / expected_id).mkdir(parents=True)

    with pytest.raises(TopicGraphError) as excinfo:
        downgrade_topic_to_proposal(fake_git_repo, "overview")
    assert expected_id in str(excinfo.value)
    assert "refresh" in str(excinfo.value).lower()

    # Topic must still be in the graph — collision should be detected
    # BEFORE the topics.pop / save_graph mutation.
    graph = load_graph(fake_git_repo)
    assert "overview" in graph["topics"]


def test_regenerate_downgraded_proposal_creates_new_revision(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo, seeds=True)
    downgraded = downgrade_topic_to_proposal(fake_git_repo, "overview")

    result = regenerate_proposal_run(fake_git_repo, downgraded["id"])

    assert result["dir"].name == downgraded["id"]
    assert load_proposal(fake_git_repo, downgraded["id"]) is not None
    revisions = list_proposal_revisions(fake_git_repo, downgraded["id"])
    assert [revision["revision_number"] for revision in revisions] == [2, 1]


def test_downgrade_preserves_existing_per_topic_wiki(stub_proposal_provider, fake_git_repo):
    """If the approved topic has a rich `.regin/topics/wiki/<id>.md`
    (from a prior accept), the downgrade carries it into the new
    proposal's `wiki.md` instead of dropping the user's narrative.
    """
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("service")
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["service"] = {
        "label": "Service",
        "aliases": ["svc"],
        "intent": "Service.",
        "status": "active",
        "refs": [{"path": "service/api.py", "role": "implementation"}],
        "edges": [], "commands": [],
        "include_globs": ["service/**"], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    # Plant a rich, hand-authored narrative under the approved wiki path.
    rich = (
        "# Service (custom narrative)\n\n"
        "Detailed architecture notes the user wrote between accept and downgrade.\n"
        "Includes references and call edges the synthetic template can't reproduce.\n"
    )
    approved_wiki = fake_git_repo / ".regin/topics/wiki/service.md"
    approved_wiki.parent.mkdir(parents=True, exist_ok=True)
    approved_wiki.write_text(rich)

    result = downgrade_topic_to_proposal(fake_git_repo, "service")

    proposal_wiki = fake_git_repo / ".regin/topics/proposals" / result["id"] / "wiki.md"
    assert proposal_wiki.exists()
    assert "custom narrative" in proposal_wiki.read_text()
    assert "Detailed architecture notes" in proposal_wiki.read_text()
    # Downgrade used to call save_proposal before resolving the wiki,
    # leaving revision.wiki_md='' in ORM even when the disk file was rich.
    # The workspace then served an empty wiki on subsequent loads.
    reloaded = load_proposal(fake_git_repo, result["id"])
    assert "custom narrative" in (reloaded.get("wiki") or "")


# ── Wiki-narrative preservation (bug from proposal 20260519T163610Z) ──


def _seed_proposal_with_agent_wiki(repo, run_id, *, topic_id, label, agent_wiki):
    """Create a proposal whose wiki.md is the agent's rich narrative."""
    (repo / topic_id).mkdir(parents=True, exist_ok=True)
    (repo / topic_id / "core.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(repo), "add", "."])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", topic_id])
    bootstrap(repo)
    paths = create_proposal_run(repo, run_id=run_id)
    proposal = load_proposal(repo, run_id)
    proposal["topics"] = [{
        "id": topic_id,
        "label": label,
        "aliases": [],
        "intent": f"Curated context for {label}.",
        "status": "active",
        "refs": [{"path": f"{topic_id}/core.py", "role": "implementation"}],
        "edges": [],
        "commands": [],
        "include_globs": [f"{topic_id}/**"],
        "exclude_globs": [],
        "evidence_paths": [f"{topic_id}/core.py"],
    }]
    save_proposal(repo, run_id, proposal)
    # Overwrite the auto-rendered wiki.md with the agent's rich narrative.
    paths["wiki"].write_text(agent_wiki)
    return paths


def test_accept_preserves_agent_wiki_in_proposal_dir(stub_proposal_provider, fake_git_repo):
    agent_wiki = (
        "# Regin Route Mechanism\n\n"
        "## 1. Pattern routing — hybrid dense + lexical\n\n"
        "The pattern router answers which SKILL.md is most relevant by combining "
        "FTS5 BM25 and dense embeddings, fused via RRF.\n\n"
        "**Index layer**: `patterns_fts`, `pattern_embeddings`.\n"
    )
    _seed_proposal_with_agent_wiki(
        fake_git_repo,
        "run1",
        topic_id="pattern-routing",
        label="Pattern routing (hybrid dense + lexical)",
        agent_wiki=agent_wiki,
    )

    accept_proposed_topic(fake_git_repo, "run1", "pattern-routing")

    proposal_wiki = (fake_git_repo / ".regin/topics/proposals/run1/wiki.md").read_text()
    assert proposal_wiki == agent_wiki, "accept must not overwrite the agent's narrative"


def test_accept_writes_full_proposal_wiki_to_per_topic_page(stub_proposal_provider, fake_git_repo):
    """The full proposal wiki.md becomes the per-topic page.

    Previous behaviour sliced a section by heading-overlap heuristic;
    that was brittle (silent misses + re-accept clobbering richer
    content with thinner sections). Now we copy the whole agent
    narrative verbatim — redundant on multi-topic proposals but
    never lossy.
    """
    agent_wiki = (
        "# Regin Route Mechanism\n\n"
        "## Pattern routing — hybrid dense + lexical retrieval\n\n"
        "Detailed agent narrative: FTS upsert, dense leg, RRF fusion.\n\n"
        "**Index layer**: patterns_fts + pattern_embeddings.\n\n"
        "## Topic routing — approved graph match\n\n"
        "Alias / label / ref-path scoring.\n"
    )
    _seed_proposal_with_agent_wiki(
        fake_git_repo,
        "run1",
        topic_id="pattern-routing",
        label="Pattern routing (hybrid dense + lexical)",
        agent_wiki=agent_wiki,
    )

    accept_proposed_topic(fake_git_repo, "run1", "pattern-routing")

    page = fake_git_repo / ".regin/topics/wiki/pattern-routing.md"
    assert page.exists(), "per-topic wiki page should be written on accept"
    text = page.read_text()
    # Full agent wiki — both topic sections are present in the page.
    assert "Pattern routing" in text
    assert "FTS upsert" in text
    assert "Topic routing — approved graph match" in text


def test_accept_persists_per_topic_wiki_even_without_matching_heading(stub_proposal_provider, fake_git_repo):
    """No heading match → still write the full proposal wiki.

    Previous behaviour: silently wrote no file when the heading
    matcher missed → user saw boilerplate. New behaviour: the file
    is always written so the agent narrative is never lost.
    """
    agent_wiki = (
        "# Routing Mechanisms Overview\n\n"
        "## 1. Database connection pooling\n\n"
        "Unrelated section discussing pool sizing and retry policy.\n"
    )
    _seed_proposal_with_agent_wiki(
        fake_git_repo,
        "run1",
        topic_id="auth-flow",
        label="Authentication Flow",
        agent_wiki=agent_wiki,
    )

    accept_proposed_topic(fake_git_repo, "run1", "auth-flow")

    page = fake_git_repo / ".regin/topics/wiki/auth-flow.md"
    assert page.exists()
    assert "Routing Mechanisms Overview" in page.read_text()


# ── scan(staged=True) must not zero out an existing topic's refs ──


def test_staged_scan_does_not_zero_refs_for_topics_without_staged_matches(stub_proposal_provider, fake_git_repo):
    from lib.topics.scan import scan

    # Topic with include_globs covering libfoo/**, refs already populated.
    (fake_git_repo / "libfoo").mkdir()
    (fake_git_repo / "libfoo" / "core.py").write_text("# x\n")
    (fake_git_repo / "libfoo" / "util.py").write_text("# y\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "libfoo"])
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["libfoo"] = {
        "label": "libfoo",
        "intent": "Curated.",
        "status": "active",
        "aliases": [],
        "refs": [
            {"path": "libfoo/core.py", "role": "implementation"},
            {"path": "libfoo/util.py", "role": "implementation"},
        ],
        "edges": [],
        "commands": [],
        "include_globs": ["libfoo/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    # Stage a totally unrelated file (does not match libfoo/**).
    (fake_git_repo / "unrelated.md").write_text("# doc\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "unrelated.md"])

    scan(fake_git_repo, staged=True)

    refreshed = load_graph(fake_git_repo)["topics"]["libfoo"]
    refreshed_paths = sorted(ref["path"] for ref in refreshed["refs"])
    assert refreshed_paths == ["libfoo/core.py", "libfoo/util.py"], (
        f"staged scan zeroed out unrelated topic's refs: {refreshed['refs']}"
    )


def test_proposal_run_row_reports_external_agent_while_running(tmp_path):
    """Running external-agent proposals must show as 'external-agent', not 'unknown'.

    Before topics.json is written, the only data is status.json (with
    `agent` set). The row builder must detect the agent name and label
    the row as external-agent.
    """
    from web.blueprints.topics._helpers import _proposal_run_row

    # Simulate the listing row that list_proposal_runs would produce
    # for an external-agent run that's still queued — has status.json
    # with agent name, but no topics.json yet.
    run = {
        "id": "20260519T200000Z",
        "path": str(tmp_path / "20260519T200000Z"),
        "has_topics": False,
        "has_evidence": True,
        "has_wiki": False,
        "state": "queued",
        "trace_id": "topic-proposal-20260519T200000Z",
        "agent": "claude",
        "error": None,
    }
    row = _proposal_run_row(str(tmp_path), run)
    assert row["provider"] == "external-agent"

    # A row with no agent and no topics.json — shouldn't normally
    # happen — falls back to "unknown" rather than guessing.
    run_no_agent = {**run, "agent": None}
    row_no_agent = _proposal_run_row(str(tmp_path), run_no_agent)
    assert row_no_agent["provider"] == "unknown"


def test_proposal_run_row_title_derivation(monkeypatch):
    """Title is derived from the topic_request, else the topics list.

    Guards the run-row title branches: topic_request wins; otherwise a
    single topic uses its label, multiple topics get a "X + N more"
    summary, and an empty proposal yields None.
    """
    from web.blueprints.topics import _helpers

    run = {"id": "r", "path": "/nonexistent", "has_topics": True}

    def _row_for(proposal):
        monkeypatch.setattr(_helpers, "load_proposal", lambda repo, rid: proposal)
        return _helpers._proposal_run_row("/repo", run)

    # topic_request takes precedence over topics.
    row = _row_for({"topic_request": "  ship the auth flow  ", "topics": [{"label": "ignored"}]})
    assert row["title"] == "ship the auth flow"
    assert row["topic_request"] == "ship the auth flow"

    # Single topic with no request -> its label.
    row = _row_for({"topics": [{"label": "Auth"}]})
    assert row["title"] == "Auth"
    assert row["topic_request"] is None

    # Multiple topics -> "first + N more" summary.
    row = _row_for({"topics": [{"label": "Auth"}, {"label": "Billing"}, {"id": "x"}]})
    assert row["title"] == "Auth + 2 more"
    assert row["draft_topic_count"] == 3

    # No topics and no request -> None.
    row = _row_for({"topics": []})
    assert row["title"] is None


def test_external_agent_instructions_are_repo_driven(fake_git_repo):
    from lib.topics.proposal_external import _instructions

    # No evidence pack: the prompt seeds the agent with the topic request
    # and existing approved topics, then sends it to explore the repo with
    # its own tools. It must not reference an evidence.json artifact.
    bootstrap(fake_git_repo)
    out_dir = fake_git_repo / ".regin/topics/proposals/run1"
    text = _instructions(
        fake_git_repo, "ship it", out_dir, out_dir / ".tmp/agent-output.json",
    )

    assert "ship it" in text  # topic_request preserved
    assert len(text) < 20_000  # bounded prompt; no bulky inline dump
    assert "evidence.json" not in text
    assert "Read/Glob/Grep" in text  # told to explore the repo itself


def test_external_agent_instructions_include_review_feedback():
    from pathlib import Path
    from lib.topics.proposal_external import _instructions

    text = _instructions(
        Path("/tmp/out"),
        "auth",
        Path("/tmp/out"),
        Path("/tmp/out/.tmp/agent-output.json"),
        prior_draft={
            "proposal": {"topics": []},
            "wiki": "# Draft wiki\n",
            "feedback_threads": [{
                "proposal_topic_id": "service",
                "anchor_kind": "topic_field",
                "anchor": {"field": "intent"},
                "quoted_text": "Old intent",
                "comments": [
                    {"author_kind": "user", "body": "Please narrow this intent."},
                    {"author_kind": "user", "body": "Keep it grounded in current files."},
                ],
            }],
        },
    )

    assert "Review feedback to address in this revision:" in text
    assert "topic `service`, field `intent`" in text
    assert 'Quoted text: "Old intent"' in text
    assert "Please narrow this intent." in text
    assert "Keep it grounded in current files." in text
    assert text.index("Prior draft reference:") < text.index("Review feedback to address in this revision:")
    assert text.index("Review feedback to address in this revision:") < text.index("Previous proposal JSON:")


def test_staged_scan_adds_new_matching_refs(stub_proposal_provider, fake_git_repo):
    from lib.topics.scan import scan

    (fake_git_repo / "libfoo").mkdir()
    (fake_git_repo / "libfoo" / "core.py").write_text("# x\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "libfoo"])
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["libfoo"] = {
        "label": "libfoo",
        "intent": "Curated.",
        "status": "active",
        "aliases": [],
        "refs": [{"path": "libfoo/core.py", "role": "implementation"}],
        "edges": [],
        "commands": [],
        "include_globs": ["libfoo/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    # Stage a NEW file under libfoo/. Staged scan should add it as a ref.
    (fake_git_repo / "libfoo" / "util.py").write_text("# new\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "libfoo/util.py"])

    scan(fake_git_repo, staged=True)

    refreshed = load_graph_merged(fake_git_repo)["topics"]["libfoo"]
    paths = sorted(ref["path"] for ref in refreshed["refs"])
    assert paths == ["libfoo/core.py", "libfoo/util.py"]


def test_topic_signature_ignores_updated_at(fake_git_repo):
    from lib.topics.proposal_external import _read_topic_signature
    bootstrap(fake_git_repo)
    before = _read_topic_signature(fake_git_repo)
    # save_graph stamps a fresh updated_at every time, even when topics/aliases/edges
    # are byte-identical — that used to trip the integrity check.
    save_graph(fake_git_repo, load_graph(fake_git_repo))
    assert _read_topic_signature(fake_git_repo) == before


def test_orm_invariant_forces_failed_when_error_set_on_completed_run(fake_git_repo, tmp_db):
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_load_proposal_status, orm_update_proposal_status,
    )
    bootstrap(fake_git_repo)
    orm_create_proposal_run(fake_git_repo, "run1", provider="external-agent", state="completed")
    orm_update_proposal_status(fake_git_repo, "run1", error="external agent modified the approved topic graph")
    status = orm_load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "failed"
    assert status["error"] == "external agent modified the approved topic graph"


def test_orm_invariant_normalizes_stuck_running_when_completed_at_set(fake_git_repo, tmp_db):
    """A writer that crashed between setting completed_at and updating
    state would leave the row with state='running' AND completed_at set.
    The frontend poller treats 'running' as live forever and pings
    status/summary/proposals every 2.5s indefinitely. The write-time
    invariant pins state to 'completed' so polling stops.
    """
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_load_proposal_status, orm_update_proposal_status,
    )
    bootstrap(fake_git_repo)
    orm_create_proposal_run(fake_git_repo, "run1", provider="external-agent", state="running")
    orm_update_proposal_status(fake_git_repo, "run1", completed_at="2026-05-20T02:35:26Z")
    status = orm_load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "completed"


def test_orm_invariant_preserves_explicit_non_failed_terminal_states(fake_git_repo, tmp_db):
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_load_proposal_status, orm_update_proposal_status,
    )
    bootstrap(fake_git_repo)
    orm_create_proposal_run(fake_git_repo, "run1", provider="external-agent", state="running")
    orm_update_proposal_status(
        fake_git_repo, "run1",
        state="waiting_for_permission",
        error="external agent requested interactive permission",
    )
    status = orm_load_proposal_status(fake_git_repo, "run1")
    assert status["state"] == "waiting_for_permission"


def test_regenerate_rejects_when_run_already_in_flight(stub_proposal_provider, fake_git_repo, tmp_db):
    from lib.topics.proposal_orm import orm_update_proposal_status
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    # Simulate a regenerate already executing in a background thread by
    # flipping the run state back to "running". Mirror the real flow's
    # write_status: clear completed_at so the invariant ("completed_at
    # set ⇒ state must be completed") doesn't immediately revert state.
    orm_update_proposal_status(fake_git_repo, "run1", state="running", clear_completed_at=True)
    with pytest.raises(TopicGraphError, match="already in flight"):
        regenerate_proposal_run(fake_git_repo, "run1")
