"""Grader configuration API — GET shape + PUT validation/persistence.

Covers `web/blueprints/grader_config.py`. Persistence is exercised through a
save spy that mimics `reload_settings()` (applies the grader block to the live
singleton) so the test never writes the repo's real `config/settings.json`.
"""

from __future__ import annotations

import pytest

from lib.settings import GraderConfig, TopicProposalExternalAgent, settings


@pytest.fixture
def save_spy(monkeypatch):
    """Replace the blueprint's save_settings with a spy that applies the
    persisted grader block to the live settings singleton (as reload would)."""
    import web.blueprints.grader_config as gc
    captured: dict = {}

    def fake_save(updates, scope="auto"):
        captured["updates"] = updates
        captured["scope"] = scope
        merged = {**settings.grader.model_dump(), **updates["grader"]}
        monkeypatch.setattr(settings, "grader", GraderConfig(**merged))

    monkeypatch.setattr(gc, "save_settings", fake_save)
    return captured


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="claude"),
        "kimi": TopicProposalExternalAgent(command="kimi", args=["-p", "{prompt}"]),
    })


def test_get_config_shape(flask_client, agents):
    body = flask_client.get("/api/grader/config").get_json()
    keys = {a["key"] for a in body["aspects"]}
    assert {"correctness", "process"} <= keys
    builtin = {a["key"] for a in body["aspects"] if a["builtin"]}
    assert builtin == {"correctness", "process"}
    assert set(body["system_prompts"]) == {"correctness", "process"}
    assert body["system_prompts"]["correctness"]["default"]   # builtin prompt
    assert body["providers"] == ["claude", "kimi"]
    assert body["tiers"] and body["axes"]


def test_put_toggles_aspect_and_persists(flask_client, save_spy):
    payload = {"aspects": [
        {"key": "correctness", "label": "Correctness", "enabled": True},
        {"key": "process", "label": "Process", "enabled": True},
        {"key": "clarity", "label": "Clarity", "description": "be clear",
         "enabled": True},
    ]}
    resp = flask_client.put("/api/grader/config", json=payload)
    assert resp.status_code == 200
    aspects = {a["key"]: a for a in resp.get_json()["aspects"]}
    assert aspects["clarity"]["enabled"] and aspects["clarity"]["description"] == "be clear"
    assert save_spy["scope"] == "shared"               # grader config is shared
    assert settings.grader.aspects[-1].key == "clarity"  # live singleton updated


def test_put_cannot_delete_builtin_aspect(flask_client, save_spy):
    # Posting only a custom aspect must NOT drop correctness/process.
    resp = flask_client.put("/api/grader/config", json={
        "aspects": [{"key": "clarity", "label": "Clarity", "enabled": True}]})
    keys = {a["key"] for a in resp.get_json()["aspects"]}
    assert {"correctness", "process"} <= keys


def test_put_client_cannot_forge_builtin_flag(flask_client, save_spy):
    resp = flask_client.put("/api/grader/config", json={
        "aspects": [
            {"key": "correctness", "enabled": True},
            {"key": "evil", "label": "Evil", "builtin": True, "enabled": True},
        ]})
    aspects = {a["key"]: a for a in resp.get_json()["aspects"]}
    assert aspects["evil"]["builtin"] is False         # forced server-side


def test_put_saves_system_prompt_override(flask_client, save_spy):
    resp = flask_client.put("/api/grader/config", json={
        "system_prompt_overrides": {"process": "CUSTOM PROCESS PROMPT"}})
    assert resp.status_code == 200
    assert settings.grader.system_prompt_overrides["process"] == "CUSTOM PROCESS PROMPT"


def test_put_blank_override_is_dropped(flask_client, save_spy):
    flask_client.put("/api/grader/config", json={
        "system_prompt_overrides": {"correctness": "   "}})
    assert "correctness" not in settings.grader.system_prompt_overrides


def test_put_external_agent_validated(flask_client, save_spy, agents):
    ok = flask_client.put("/api/grader/config", json={"external_agent": "kimi"})
    assert ok.status_code == 200 and settings.grader.external_agent == "kimi"

    bad = flask_client.put("/api/grader/config", json={"external_agent": "ghost"})
    assert bad.status_code == 400 and "ghost" in bad.get_json()["error"]


def test_put_empty_payload_is_400(flask_client, save_spy):
    resp = flask_client.put("/api/grader/config", json={})
    assert resp.status_code == 400
