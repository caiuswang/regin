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


JUDGE_BATCH_SURFACE_ID = "topic-proposal-drift-judge-batch"

_JUDGE_BATCH_DEFAULT_BODY = """Several topics' ref files changed since their wikis were written. For EACH topic below, decide whether its change is MATERIAL (that topic's wiki narrative is now inaccurate or incomplete and should be re-drafted) or TRIVIAL (formatting, comments, internal refactors, or edits that don't change what the wiki says).

Each topic block carries the evidence recorded at detection time: the changed ref paths, any wiki-cited identifiers that vanished from them, a git diff of each ref against the wiki's baseline commit (when available), and the current wiki. Judge from the evidence — and use your Read/Glob/Grep tools to pull anything more you need; do not rubber-stamp the summary.

{{topic_blocks}}

<task>
Answer with exactly one line per topic, nothing else:
<topic_id>: MATERIAL|TRIVIAL — <one-sentence reason>
</task>"""


register_surface(
    JUDGE_BATCH_SURFACE_ID,
    label="Topic proposal — batched drift judge",
    area="topic-proposal",
    default_body=_JUDGE_BATCH_DEFAULT_BODY,
    description=(
        "One prompt judging every pending content-drift refresh in a sweep — "
        "per-topic git diffs + vanished wiki anchors + current wiki — instead "
        "of one triage call per topic."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("topic_blocks", "One markdown block per pending topic: changed refs with vanished-anchor notes and git-diff excerpts, plus the current wiki."),
    ),
    tags=("topic-proposal-agent", "drift-triage"),
)


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

__all__ = ["SURFACE_ID", "JUDGE_BATCH_SURFACE_ID"]
