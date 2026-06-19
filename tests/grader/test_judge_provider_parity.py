"""Judge provider selection + Kimi CLI parity.

Covers `lib/grader/judge_io.extract_json_object` (robust to a noisy judge
CLI's wrapper output), `ExternalAgentJudge` invocation building (prompt via
stdin vs a `{prompt}` arg), and `resolve_judge(agent_id=...)` provider
selection + the `--allowedTools` suppression for agents that lack the flag.
"""

from __future__ import annotations

import pytest

from lib.settings import TopicProposalExternalAgent, settings
from lib.grader.adapters import ExternalAgentJudge, resolve_judge
from lib.grader.judge_io import extract_json_object


# ── robust JSON extraction ───────────────────────────────────────

def test_extract_picks_last_object_past_wrapper_noise():
    # Kimi's shape: a hook echo first, the real answer last, then a footer.
    raw = (
        '{"suppressOutput": true}\n'
        'The user asked me to judge. Here is my verdict:\n'
        '{"claims": [{"id": "c1", "verdict": "GROUNDED"}]}\n'
        'To resume this session: kimi -r session_abc\n'
    )
    parsed = extract_json_object(raw)
    assert parsed == {"claims": [{"id": "c1", "verdict": "GROUNDED"}]}


def test_extract_ignores_braces_inside_strings():
    raw = '{"reason": "the diff added {x: 1} to the map", "verdict": "GROUNDED"}'
    assert extract_json_object(raw)["verdict"] == "GROUNDED"


def test_extract_none_when_no_object():
    assert extract_json_object("no json here") is None
    assert extract_json_object("") is None
    assert extract_json_object(None) is None


# ── invocation building (stdin vs {prompt} arg) ──────────────────

def test_invocation_stdin_for_claude_style():
    agent = TopicProposalExternalAgent(command="claude", args=["--print"])
    judge = ExternalAgentJudge(extra_args=["--allowedTools", "Bash"])
    argv, stdin = judge._invocation(agent, "JUDGE THIS")
    assert argv == ["claude", "--print", "--allowedTools", "Bash"]
    assert stdin == b"JUDGE THIS"          # prompt piped on stdin


def test_invocation_substitutes_prompt_arg_for_kimi_style():
    agent = TopicProposalExternalAgent(command="kimi", args=["-p", "{prompt}"])
    judge = ExternalAgentJudge()
    argv, stdin = judge._invocation(agent, "JUDGE THIS")
    assert argv == ["kimi", "-p", "JUDGE THIS"]
    assert stdin is None                   # no stdin when prompt is an arg


# ── provider selection + allowed-tools suppression ───────────────

@pytest.fixture
def two_agents(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="claude", args=["--print"]),
        "kimi": TopicProposalExternalAgent(
            command="kimi", args=["-p", "{prompt}"],
            supports_allowed_tools=False),
    })
    monkeypatch.setattr(settings.grader, "judge_allowed_tools", ["Bash(x:*)"])
    monkeypatch.setattr(settings.grader, "external_agent", None)


def test_resolve_judge_selects_named_provider(two_agents):
    assert resolve_judge(agent_id="kimi").judge_id == "kimi"
    assert resolve_judge(agent_id="claude").judge_id == "claude"


def test_resolve_judge_defaults_to_first_agent(two_agents):
    # No agent_id and no settings.grader.external_agent → first configured.
    assert resolve_judge().judge_id == "claude"


def test_allowed_tools_granted_for_claude_suppressed_for_kimi(two_agents):
    claude = resolve_judge(agent_id="claude")
    argv, _ = claude._invocation(claude.selected_agent, "P")
    assert "--allowedTools" in argv         # claude supports the flag

    kimi = resolve_judge(agent_id="kimi")
    argv, stdin = kimi._invocation(kimi.selected_agent, "P")
    assert "--allowedTools" not in argv     # kimi has no such flag
    assert argv == ["kimi", "-p", "P"] and stdin is None


def test_resolve_judge_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})
    assert resolve_judge(agent_id="kimi") is None
