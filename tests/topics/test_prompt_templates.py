"""Tests for prompt template CRUD + injection into the topic proposal flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.auth import create_token
from lib.prompt_templates import (
    PromptTemplateError,
    create_template,
    default_template_slugs_for,
    delete_template,
    get_template_by_slug,
    list_templates,
    update_template,
)
from lib.topics.proposal_external import _format_template_section, _instructions


def _editor_auth():
    token = create_token(1, "editor-tester", "editor")
    return {"Authorization": f"Bearer {token}"}


# ── lib.prompt_templates ──────────────────────────────────────


def test_fresh_db_ships_builtin_template(tmp_db):
    # Fresh tmp_db is built from db/schema.sql, which seeds the built-in
    # gitnexus-usage template via INSERT OR IGNORE on the unique slug.
    templates = list_templates()
    assert [t["slug"] for t in templates] == ["gitnexus-usage"]
    assert templates[0]["builtin"] is True


def test_create_update_delete_cycle(tmp_db):
    created = create_template({
        "label": "Bias to tests",
        "body": "When in doubt, propose a tests topic.",
        "applies_to": ["langchain"],
        "default_for_providers": [],
    })
    assert created["slug"] == "bias-to-tests"
    assert created["builtin"] is False
    assert get_template_by_slug("bias-to-tests")["body"].startswith("When in doubt")

    updated = update_template("bias-to-tests", {"body": "Always include a tests topic."})
    assert updated["body"] == "Always include a tests topic."

    snap = delete_template("bias-to-tests")
    assert snap["slug"] == "bias-to-tests"
    assert get_template_by_slug("bias-to-tests") is None


def test_create_template_requires_label_and_body(tmp_db):
    with pytest.raises(PromptTemplateError):
        create_template({"label": "", "body": "x"})
    with pytest.raises(PromptTemplateError):
        create_template({"label": "ok", "body": ""})


def test_default_template_slugs_for_filters_by_provider(tmp_db):
    create_template({
        "label": "Use GitNexus",
        "body": "Call gitnexus first.",
        "applies_to": ["external-agent"],
        "default_for_providers": ["external-agent"],
    })
    create_template({
        "label": "Verbose mode",
        "body": "Be verbose.",
        "applies_to": ["langchain"],
        "default_for_providers": [],
    })
    # The seeded gitnexus-usage builtin also defaults for external-agent
    # and sorts first (lower rowid than the templates created here).
    assert default_template_slugs_for("external-agent") == ["gitnexus-usage", "use-gitnexus"]
    assert default_template_slugs_for("langchain") == []


def test_create_template_dedupes_slug(tmp_db):
    a = create_template({"label": "Use GitNexus", "body": "a"})
    b = create_template({"label": "Use GitNexus", "body": "b"})
    assert a["slug"] == "use-gitnexus"
    assert b["slug"] == "use-gitnexus-2"


# ── Injection into both prompt seams ─────────────────────────


def test_format_template_section_renders_custom_block():
    text = _format_template_section([
        {"slug": "g", "label": "GitNexus", "body": "Run mcp__gitnexus__query first."},
    ])
    assert "## Custom Instructions" in text
    assert "### GitNexus" in text
    assert "Run mcp__gitnexus__query first." in text


def test_format_template_section_empty_is_noop():
    assert _format_template_section(None) == ""
    assert _format_template_section([]) == ""


def test_instructions_injects_custom_block_between_request_and_rules():
    text = _instructions(
        Path("/tmp"),
        "auth",
        Path("/tmp"),
        Path("/tmp/out.json"),
        prompt_templates=[{"slug": "g", "label": "GitNexus", "body": "Use gitnexus."}],
    )
    # Order: topic_request, Custom Instructions, Rules block
    assert text.index("auth") < text.index("## Custom Instructions") < text.index("\nRules:")
    assert "Use gitnexus." in text


# ── HTTP API ─────────────────────────────────────────────────


def test_list_endpoint_returns_builtin_on_fresh_db(flask_client, tmp_db):
    response = flask_client.get("/api/prompt-templates")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert [t["slug"] for t in data["templates"]] == ["gitnexus-usage"]


def test_crud_endpoints_round_trip(flask_client, tmp_db):
    create = flask_client.post(
        "/api/prompt-templates",
        json={"label": "Bias to tests", "body": "Prefer a tests topic."},
        headers=_editor_auth(),
    )
    assert create.status_code == 200
    slug = create.get_json()["template"]["slug"]
    assert slug == "bias-to-tests"

    patch = flask_client.patch(
        f"/api/prompt-templates/{slug}",
        json={"body": "Updated body."},
        headers=_editor_auth(),
    )
    assert patch.status_code == 200
    assert patch.get_json()["template"]["body"] == "Updated body."

    delete = flask_client.delete(f"/api/prompt-templates/{slug}", headers=_editor_auth())
    assert delete.status_code == 200

    # The seeded built-in remains after the custom template is deleted.
    listing = flask_client.get("/api/prompt-templates").get_json()
    assert [t["slug"] for t in listing["templates"]] == ["gitnexus-usage"]


def test_create_requires_auth(anon_client, tmp_db):
    response = anon_client.post(
        "/api/prompt-templates",
        json={"label": "X", "body": "Y"},
    )
    assert response.status_code in (401, 403)


def test_delete_builtin_template_rejected(flask_client, tmp_db):
    # db/schema.sql already seeds the built-in gitnexus-usage row, so no
    # manual setup is needed to exercise the delete-builtin guard.
    response = flask_client.delete("/api/prompt-templates/gitnexus-usage", headers=_editor_auth())
    assert response.status_code == 400
    assert "built-in" in response.get_json()["error"]


# ── Plumbing: regenerate retains prompt_template_ids ──────────


def test_create_proposal_run_persists_prompt_template_ids_on_metadata(tmp_db, tmp_path):
    # Stub the drafting seam to confirm the resolved template slug list
    # flows through to the agent and is persisted on the run metadata.
    from lib.topics import proposals as proposals_mod

    captured = {}

    def fake_draft_proposal(*, repo, out_dir, proposal_id, topic_request=None, scope="all", agent=None, prior_draft=None, prompt_templates=None):
        captured["prompt_templates"] = prompt_templates
        proposal = {
            "version": 1,
            "repo": str(repo),
            "scope": "all",
            "generated_at": "2026-05-19T00:00:00+00:00",
            "status": "draft",
            "topics": [],
            "notes": [],
            "metadata": {
                "prompt_template_ids": [t["slug"] for t in (prompt_templates or [])],
            },
        }
        return proposal, "wiki text"

    create_template({"label": "GitNexus", "body": "Call gitnexus.", "applies_to": ["external-agent"]})

    from lib.topics.proposals import core_io as core_io_mod
    monkeypatch_target = core_io_mod._draft_proposal
    core_io_mod._draft_proposal = fake_draft_proposal
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# r")
        paths = proposals_mod.create_proposal_run(
            repo,
            prompt_template_ids=["gitnexus"],
        )
    finally:
        core_io_mod._draft_proposal = monkeypatch_target

    assert [t["slug"] for t in captured["prompt_templates"]] == ["gitnexus"]
    saved = proposals_mod.load_proposal(repo, paths["dir"].name)
    assert saved["metadata"]["prompt_template_ids"] == ["gitnexus"]
