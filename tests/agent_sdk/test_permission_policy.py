"""What an SDK-owned session parks, and what the parked card says.

The tier's permission story used to be one tool: `AskUserQuestion`. Gating a
plan or a real tool call is a strictly larger power, so the load-bearing test
here is the *default* — an install that never asked for gating must behave
exactly as it did, or turning regin's own agent loose becomes a different act
than the operator agreed to.
"""

from __future__ import annotations

import pytest

from lib.agent_sdk import policy
from lib.settings import settings


@pytest.fixture
def gates(monkeypatch):
    """Both gates in their shipped (off) position, mutable per test."""
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", False)
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", [])
    return settings.agent_sdk


def test_by_default_only_a_question_parks(gates):
    assert policy.permission_kind("AskUserQuestion") == "question"
    for tool in ("Bash", "Edit", "ExitPlanMode", "mcp__linear__create_issue"):
        assert policy.permission_kind(tool) is None


def test_a_question_parks_even_with_every_gate_off(gates):
    """Auto-allowing an ask would run the tool with no answers at all."""
    assert policy.permission_kind("AskUserQuestion") == "question"


def test_gate_plan_parks_exit_plan_mode_as_a_plan(gates, monkeypatch):
    monkeypatch.setattr(gates, "gate_plan", True)

    assert policy.permission_kind("ExitPlanMode") == "plan"
    assert policy.permission_kind("Bash") is None


def test_a_named_tool_parks_as_a_tool(gates, monkeypatch):
    monkeypatch.setattr(gates, "gated_tools", ["Bash"])

    assert policy.permission_kind("Bash") == "tool"
    assert policy.permission_kind("Read") is None


def test_the_wildcard_gates_tools_regin_cannot_enumerate(gates, monkeypatch):
    """MCP tools arrive with server-specific names, so naming them is not an
    option for "approve everything from my phone"."""
    monkeypatch.setattr(gates, "gated_tools", ["*"])

    assert policy.permission_kind("mcp__linear__create_issue") == "tool"
    assert policy.permission_kind("Bash") == "tool"
    # The question keeps its own kind — it is answered, not decided.
    assert policy.permission_kind("AskUserQuestion") == "question"


def test_plan_gating_wins_over_the_wildcard(gates, monkeypatch):
    monkeypatch.setattr(gates, "gate_plan", True)
    monkeypatch.setattr(gates, "gated_tools", ["*"])

    assert policy.permission_kind("ExitPlanMode") == "plan"


def test_a_gated_bash_card_shows_the_command(gates):
    attrs = policy.request_attrs("Bash", {"command": "rm -rf build"}, "tool")

    assert attrs["command_preview"] == "rm -rf build"
    assert attrs["requested_permission"] == "Run shell command: rm -rf build"


def test_a_gated_edit_card_names_the_file(gates):
    attrs = policy.request_attrs("Edit", {"file_path": "/repo/app.py"}, "tool")

    assert attrs["requested_permission"] == "Modify file: /repo/app.py"
    assert "command_preview" not in attrs


def test_an_unknown_tool_still_produces_a_readable_line(gates):
    attrs = policy.request_attrs("mcp__x__do", {}, "tool")

    assert attrs["requested_permission"] == "Use tool: mcp__x__do"


def test_a_plan_card_carries_the_plan_text(gates):
    attrs = policy.request_attrs("ExitPlanMode", {"plan": "# Step 1\nrewrite"},
                                 "plan")

    assert attrs["plan"] == "# Step 1\nrewrite"
    assert attrs["requested_permission"] == "Approve the plan and start building"


def test_a_plan_is_capped_so_one_span_cannot_carry_a_novel(gates):
    attrs = policy.request_attrs("ExitPlanMode", {"plan": "x" * 20_000}, "plan")

    assert len(attrs["plan"]) == 8000


def test_only_a_plan_kind_carries_plan_text(gates):
    """A tool whose input happens to have a `plan` key is not a plan card."""
    attrs = policy.request_attrs("Bash", {"command": "ls", "plan": "nope"},
                                 "tool")

    assert "plan" not in attrs


def test_an_allow_decision_reads_as_allow():
    assert policy.decision_outcome({"behavior": "allow"}) == ("allow", "")


def test_a_deny_without_a_reason_still_tells_the_model_who_refused():
    behavior, detail = policy.decision_outcome({"behavior": "deny"})

    assert behavior == "deny"
    assert detail == "Denied by operator"


def test_a_deny_reason_is_carried_to_the_model():
    assert policy.decision_outcome(
        {"behavior": "deny", "reason": "use the staging bucket"}
    ) == ("deny", "use the staging bucket")


def test_an_unanswered_decision_denies():
    """A dismissal is the session going away under an operator who never
    decided; allowing would run a gated tool nobody approved."""
    assert policy.decision_outcome(None) == ("deny", "Dismissed by operator")


def test_an_unrecognized_behavior_is_not_an_allow():
    behavior, _ = policy.decision_outcome({"behavior": "maybe"})

    assert behavior == "deny"


def test_a_shadowing_permission_mode_is_reported_not_silently_obeyed(
        monkeypatch):
    """`acceptEdits` skips the callback with no SDK warning at all, so an
    operator can configure gating and get a runtime that gates nothing."""
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", ["Write"])

    for mode in ("acceptEdits", "bypassPermissions"):
        monkeypatch.setattr(settings.agent_sdk, "permission_mode", mode)
        assert mode in settings.agent_sdk.shadowed_gating()

    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "default")
    assert settings.agent_sdk.shadowed_gating() == ""


def test_gating_nothing_is_never_reported_as_shadowed(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "acceptEdits")
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", [])
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", False)

    assert settings.agent_sdk.shadowed_gating() == ""


def test_a_plan_gated_as_a_plain_tool_still_shows_its_text(monkeypatch):
    """`gated_tools=["*"]` parks ExitPlanMode as kind='tool'; without the
    body the operator approves a plan they cannot read."""
    attrs = policy.request_attrs(policy.PLAN_TOOL, {"plan": "step one"}, "tool")

    assert attrs["plan"] == "step one"
