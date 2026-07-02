"""Characterization test: the editable review skeleton renders byte-identical
to the pre-refactor hardcoded f-string.

``_reference_build_prompt`` below is a **frozen copy** of what
``proposal_review._build_prompt`` produced before the dynamic-prompt-template
refactor. It is fully self-contained (the review builder had no shared helpers),
so the only thing under test is the migrated template body + the context wiring
in the new ``_build_prompt``. If the two ever diverge, the migration dropped or
mangled text — edit the surface body and this reference together.
"""

from __future__ import annotations

from typing import Any

import lib.topics.proposal_review as pr


def _reference_build_prompt(proposal: dict[str, Any], open_feedback: str) -> str:
    topics = proposal.get("topics") or []
    topic_lines = []
    for topic in topics:
        tid = topic.get("id", "?")
        refs = ", ".join(
            r.get("path", "") for r in topic.get("refs", []) if isinstance(r, dict)
        )
        topic_lines.append(f"- {tid}: {topic.get('intent', '')}\n  refs: {refs}")
    feedback_block = (
        f"<prior_open_feedback>\n{open_feedback}\n</prior_open_feedback>\n\n"
        if open_feedback else ""
    )
    return (
        "You are reviewing a proposed topic-graph draft for this repository. "
        "Use your Read/Glob/Grep tools to check the listed ref files as they "
        "exist NOW and judge whether the draft is accurate and worth applying.\n\n"
        "<draft_topics>\n" + "\n".join(topic_lines) + "\n</draft_topics>\n\n"
        + feedback_block +
        "<task>\n"
        "Assess coverage, accuracy against the current code, and whether any "
        "prior open feedback is addressed. Be precise — only raise real "
        "problems, not stylistic nitpicks. End with exactly one line:\n"
        "RECOMMENDATION: ACCEPT|REGENERATE|DISMISS\n"
        "  ACCEPT   — the draft is sound; apply it as is.\n"
        "  REGENERATE — there are fixable gaps; re-draft addressing them.\n"
        "  DISMISS  — the proposal isn't worth pursuing.\n"
        "</task>"
    )


def _run(proposal: dict[str, Any], open_feedback: str) -> tuple[str, str]:
    expected = _reference_build_prompt(proposal, open_feedback)
    actual = pr._build_prompt(proposal, open_feedback)
    return expected, actual


def test_parity_no_topics_no_feedback():
    # Edge case: 0 topics and empty open_feedback (the loading/empty state).
    expected, actual = _run({"topics": []}, "")
    assert actual == expected


def test_parity_single_topic_no_feedback():
    proposal = {
        "topics": [
            {"id": "auth", "intent": "How login works",
             "refs": [{"path": "lib/auth.py"}, {"path": "lib/session.py"}]},
        ],
    }
    expected, actual = _run(proposal, "")
    assert actual == expected


def test_parity_many_topics_with_feedback():
    proposal = {
        "topics": [
            {"id": "auth", "intent": "How login works",
             "refs": [{"path": "lib/auth.py"}, "not-a-dict"]},
            {"id": "trace", "intent": "",
             "refs": []},
            {"intent": "no id here", "refs": [{"path": "x.py"}]},
        ],
    }
    open_feedback = "- reviewer: sharpen the auth intent\n- reviewer: cite session.py"
    expected, actual = _run(proposal, open_feedback)
    assert actual == expected
    # sanity: the feedback block is present when open_feedback is non-empty.
    assert "<prior_open_feedback>" in actual
