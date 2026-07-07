"""The shared authoring-standards fragment must be injected into BOTH the
drafting and the review prompt — one bar for author and reviewer, no drift."""

from __future__ import annotations

from lib.prompts import get_surface, render_surface

FRAGMENT = "topic-authoring-standards"
INCLUDE = "{{include:topic-authoring-standards}}"


def test_fragment_is_registered():
    assert get_surface(FRAGMENT) is not None


def test_both_prompts_reference_the_shared_fragment():
    assert INCLUDE in get_surface("topic-proposal-drafting").default_body()
    assert INCLUDE in get_surface("topic-proposal-review").default_body()


def test_review_render_expands_fragment_and_reframes_task():
    out = render_surface(
        "topic-proposal-review",
        {"topic_lines": "T", "sibling_block": "", "feedback_block": ""},
    )
    assert "{{include" not in out  # the include resolved
    # the key standard that stops the reviewer rewarding a god-topic
    assert "not a file-by-file catalog" in out
    # the reframed task that judges against the standards, not raw coverage
    assert "do not just measure coverage" in out
