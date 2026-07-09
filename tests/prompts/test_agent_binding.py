"""Per-surface agent binding — a goal-prompt skeleton bound to an external agent.

Covers the four acceptance axes: binding CRUD + validation, NULL == today's
default (parity), the explicit → binding → default precedence, and the three
dispatch consumers (topic-proposal / memory / grader) honoring the binding.
"""

from __future__ import annotations

import pytest

from lib.settings import TopicProposalExternalAgent, settings
from lib.prompt_templates import (
    PromptTemplateError,
    seed_builtin_skeletons,
    surface_agent_binding,
    update_template,
)
from lib.prompts import configured_agents, default_agent_id, grader_bound_agent, surface_agent
from lib.prompts.surfaces.drafting import SURFACE_ID as DRAFTING
from lib.prompts.surfaces.memory import DISTILL_SURFACE_ID


@pytest.fixture
def two_agents(monkeypatch):
    """Two configured agents: `claude` (default) and `codex`."""
    registry = {
        "claude": TopicProposalExternalAgent(command="claude"),
        "codex": TopicProposalExternalAgent(command="codex"),
    }
    monkeypatch.setattr(settings, "topic_proposal_external_agents", registry)
    return registry


@pytest.fixture
def no_agents(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})


# --- registry views -------------------------------------------------------

def test_configured_agents_and_default(two_agents):
    ids = [a["id"] for a in configured_agents()]
    assert ids == ["claude", "codex"]  # sorted
    assert default_agent_id() == "claude"


def test_default_prefers_codex_when_no_claude(monkeypatch):
    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"codex": TopicProposalExternalAgent(command="codex"),
         "kimi": TopicProposalExternalAgent(command="kimi")},
    )
    assert default_agent_id() == "codex"


# --- binding CRUD + validation --------------------------------------------

def test_bind_persists_and_reads_back(tmp_db, two_agents):
    seed_builtin_skeletons()
    assert surface_agent_binding(DRAFTING) is None
    row = update_template(DRAFTING, {"agent": "codex"})
    assert row["agent"] == "codex"
    assert surface_agent_binding(DRAFTING) == "codex"


def test_bind_empty_clears(tmp_db, two_agents):
    seed_builtin_skeletons()
    update_template(DRAFTING, {"agent": "codex"})
    row = update_template(DRAFTING, {"agent": ""})
    assert row["agent"] is None
    assert surface_agent_binding(DRAFTING) is None


def test_bind_unknown_agent_rejected(tmp_db, two_agents):
    seed_builtin_skeletons()
    with pytest.raises(PromptTemplateError, match="unknown external agent"):
        update_template(DRAFTING, {"agent": "gpt-9000"})
    # Rejection leaves the row unbound, not half-written.
    assert surface_agent_binding(DRAFTING) is None


def test_absent_agent_key_leaves_binding_untouched(tmp_db, two_agents):
    seed_builtin_skeletons()
    update_template(DRAFTING, {"agent": "codex"})
    update_template(DRAFTING, {"body": "unrelated edit {{topic_request}}"})
    assert surface_agent_binding(DRAFTING) == "codex"


# --- parity: NULL binding == prior default --------------------------------

def test_surface_agent_none_when_unbound(tmp_db, two_agents):
    seed_builtin_skeletons()
    assert surface_agent(DRAFTING) is None


def test_binding_to_deconfigured_agent_resolves_none(tmp_db, two_agents, monkeypatch):
    seed_builtin_skeletons()
    update_template(DRAFTING, {"agent": "codex"})
    # codex later removed from the registry → binding must not resurrect it.
    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="claude")},
    )
    assert surface_agent(DRAFTING) is None


def test_surface_agent_never_raises_without_table(monkeypatch):
    # No tmp_db → the prompt_templates table is absent; must degrade, not crash.
    assert surface_agent(DRAFTING) is None


# --- precedence in the topic-proposal resolver ----------------------------

def test_proposal_resolver_precedence(tmp_db, two_agents):
    from lib.topics.proposal_external import _resolve_agent_config

    seed_builtin_skeletons()
    # unbound → global default (claude)
    assert _resolve_agent_config(None)[0] == "claude"
    update_template(DRAFTING, {"agent": "codex"})
    # bound → binding wins over default
    assert _resolve_agent_config(None)[0] == "codex"
    # explicit request pick still beats the binding
    assert _resolve_agent_config("claude")[0] == "claude"


# --- memory consumer honors the binding -----------------------------------

def test_memory_llm_selects_bound_agent(tmp_db, two_agents):
    from lib.memory.adapters import ExternalAgentLLM

    seed_builtin_skeletons()
    llm = ExternalAgentLLM(surface_id=DISTILL_SURFACE_ID)
    # unbound → first configured (parity with prior next(iter(...)))
    assert llm._agent().command == "claude"
    update_template(DISTILL_SURFACE_ID, {"agent": "codex"})
    assert llm._agent().command == "codex"
    # per-call surface override (reflect's shared llm path)
    update_template(DRAFTING, {"agent": "codex"})
    assert ExternalAgentLLM()._agent(DRAFTING).command == "codex"


def test_memory_llm_no_agents_returns_none(tmp_db, no_agents):
    from lib.memory.adapters import ExternalAgentLLM

    assert ExternalAgentLLM(surface_id=DISTILL_SURFACE_ID)._agent() is None


# --- grader consumer honors the binding -----------------------------------

def test_grader_bound_agent_from_either_surface(tmp_db, two_agents):
    seed_builtin_skeletons()
    assert grader_bound_agent() is None
    update_template("grader-process", {"agent": "codex"})
    assert grader_bound_agent() == "codex"


def test_grader_bound_agent_from_live_combined_surface(tmp_db, two_agents):
    # grader-combined-role is what build_combined_prompt actually renders for
    # a live deep-grading run — a binding there must resolve too, not just on
    # the standalone (dead) grader-correctness/grader-process surfaces.
    seed_builtin_skeletons()
    update_template("grader-combined-role", {"agent": "codex"})
    assert grader_bound_agent() == "codex"


def test_resolve_judge_uses_binding(tmp_db, two_agents, monkeypatch):
    from lib.grader.adapters import resolve_judge

    seed_builtin_skeletons()
    monkeypatch.setattr(settings.grader, "external_agent", None)
    monkeypatch.setattr(settings.grader, "judge_allowed_tools", [])
    update_template("grader-correctness", {"agent": "codex"})
    judge = resolve_judge()
    assert judge is not None
    assert judge.judge_id == "codex"
    # explicit agent_id still wins over the binding
    assert resolve_judge(agent_id="claude").judge_id == "claude"
