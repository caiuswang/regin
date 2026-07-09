"""The topic-proposal *drift-triage* agent prompt — asks whether a topic's
changed refs are MATERIAL (warrant a re-draft) or TRIVIAL. Migrated verbatim
from the f-string that was ``agent_spawn._triage_prompt`` (only ``{{ … }}`` is a
placeholder; the ``<topic_id>`` / ``<changed_refs>`` / ``<current_wiki>`` tags and
the ``VERDICT: MATERIAL|TRIVIAL`` line are literal text)."""

from __future__ import annotations

from lib.prompts.registry import PromptVariable, register_surface

SURFACE_ID = "topic-proposal-drift-triage"

# NOTE: keep byte-identical to the old f-string output — a characterization test
# (tests/prompts/test_triage_parity.py) asserts render_surface == the frozen
# reference builder. Edit the wording here and the reference together.
_DEFAULT_BODY = """A topic's ref files changed since its wiki was written. Decide whether the change is MATERIAL (the wiki narrative below is now inaccurate or incomplete and should be re-drafted) or TRIVIAL (formatting, comments, renames, or edits that don't change what the wiki says).

Use your Read/Glob/Grep tools to read the changed files as they exist NOW, then compare against the wiki.

<topic_id>{{topic_id}}</topic_id>

<changed_refs>
{{changed_refs}}
</changed_refs>

<current_wiki>
{{current_wiki}}
</current_wiki>

<task>
Read the changed refs, then answer with exactly one line:
VERDICT: MATERIAL|TRIVIAL
</task>"""


register_surface(
    SURFACE_ID,
    label="Topic proposal — drift triage",
    area="topic-proposal",
    default_body=_DEFAULT_BODY,
    description=(
        "The task prompt piped to the external agent that decides whether a "
        "topic's changed refs are MATERIAL (re-draft) or TRIVIAL (skip)."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_id", "The id of the topic whose refs drifted."),
        PromptVariable("changed_refs", "Bullet list of the changed ref paths, or the `- (this topic's refs)` fallback."),
        PromptVariable("current_wiki", "The topic's current wiki markdown, or `(no wiki on file)` when empty."),
    ),
    tags=("topic-proposal-agent", "drift-triage"),
)

__all__ = ["SURFACE_ID"]
