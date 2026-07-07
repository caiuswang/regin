"""The topic-proposal *review* agent prompt — the agentic reviewer that assesses
a drafted proposal against the current repo and emits a single recommendation.
Migrated verbatim from the f-string that was
``proposal_review._build_prompt`` (only ``{{ … }}`` is a placeholder; the
``<draft_topics>`` / ``<task>`` tags and the ``RECOMMENDATION:`` line are literal
text)."""

from __future__ import annotations

from lib.prompts.registry import PromptVariable, register_surface

SURFACE_ID = "topic-proposal-review"

# NOTE: keep byte-identical to the old f-string output — a characterization test
# (tests/prompts/test_review_parity.py) asserts render_surface == the frozen
# reference builder. Edit the wording here and the reference together.
_DEFAULT_BODY = """You are reviewing a proposed topic-graph draft for this repository. Use your Read/Glob/Grep tools to check the listed ref files as they exist NOW and judge whether the draft is accurate and worth applying.

<draft_topics>
{{topic_lines}}
</draft_topics>

{{sibling_block}}{{feedback_block}}<task>
Assess coverage, accuracy against the current code, and whether any prior open feedback is addressed. Be precise — only raise real problems, not stylistic nitpicks. Also check each ref's `tier`: `"reference"` means pointer-only/context (excluded from content-drift, needs no wiki prose); `"primary"`/absent means the wiki should describe it. Flag mis-tiered refs — a central implementation file marked `reference`, or a pointer-only/example file left `primary` (which nags for drift refreshes). If a <sibling_topics> block is present, also verify the draft does not duplicate a sibling's territory: open the siblings' wiki pages and flag any drafted section that substantially restates one, naming which topic should own the material. End with exactly one line:
RECOMMENDATION: ACCEPT|REGENERATE|DISMISS
  ACCEPT   — the draft is sound; apply it as is.
  REGENERATE — there are fixable gaps; re-draft addressing them.
  DISMISS  — the proposal isn't worth pursuing.
</task>"""


register_surface(
    SURFACE_ID,
    label="Topic proposal — review agent",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The task prompt piped to the external agent that reviews a drafted "
        "topic-graph proposal against the current repo and returns a single "
        "ACCEPT / REGENERATE / DISMISS recommendation."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_lines", "One bullet per drafted topic: `- <id>: <intent>` plus its ref paths."),
        PromptVariable("sibling_block", "The `<sibling_topics>` block listing same-bucket approved neighbours with their wiki paths, or empty when the draft has none.", required=False),
        PromptVariable("feedback_block", "The `<prior_open_feedback>` block replaying still-open human threads, or empty when there is none.", required=False),
    ),
)

__all__ = ["SURFACE_ID"]
