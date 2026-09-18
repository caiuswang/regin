"""The task-run launch prompt — every sentence a regin task says to its agent.

The prompt used to be an f-string assembled in ``lib.tasks.runs._prompt`` and
its four block builders, which made it the one agent-facing prompt in regin
that the operator could not change. Each block is a surface here, and the
envelope (``task-run-launch``) is the one that decides their order — so a
project that wants a run told something regin never thought of adds it there
rather than in Python.

The leading ``\\n\\n`` separator that precedes every block is NOT part of these
bodies: it stays in `runs.py`, because a block renders to the empty string when
it does not apply and only the caller knows that. An editable body therefore
starts at its own ``<tag>``.
"""

from __future__ import annotations

from lib.prompts.registry import (
    PromptVariable,
    register_retired_default,
    register_surface,
)

LAUNCH_SURFACE_ID = "task-run-launch"
TRACKER_SURFACE_ID = "task-run-tracker"
FOLLOWUP_SURFACE_ID = "task-run-followup"
FEATURE_SURFACE_ID = "task-run-feature"
FINISH_SURFACE_ID = "task-run-finish"
FINISH_ORDER_LINKED_SURFACE_ID = "task-run-finish-order-linked"
FINISH_ORDER_NATIVE_SURFACE_ID = "task-run-finish-order-native"
FINISH_BASE_UNNAMED_SURFACE_ID = "task-run-finish-base-unnamed"
FOLLOWUP_OMITTED_SURFACE_ID = "task-run-followup-omitted"

# NOTE: tests/prompts/test_task_run_parity.py asserts `runs._prompt` == a
# frozen reference builder, so the port stays provable against the f-strings it
# replaced. Edit the wording here and that reference together.
_LAUNCH_BODY = (
    "{{invocation}}{{body}}{{tracker}}{{followup}}{{feature}}{{finish}}"
)

_TRACKER_BODY = """<tracker>
This work is tracked as {{tracker_name}} {{external_ref}}. The issue — and its comments, which can revise or reverse the description — is the source of truth for what is being asked; regin's copy of the title and details is a snapshot and may already be stale.
  {{external_url}}
Read the issue and its comments before you start. When the work lands, record it there: say what changed, and move the issue's state to match. Do that before you run the finish command below — regin re-reads the issue at that moment, so a state you change afterwards is not the one the feature will show.
</tracker>"""

_FOLLOWUP_BODY = """<followup>
Comments left on this task that nobody has resolved. They were written on the task page, possibly after an earlier attempt, and they are still here because the conversation is still open — read them as part of what is being asked, not as history already dealt with. Later ones correct earlier ones. They keep arriving on every run until someone resolves the thread.
{{comments}}{{omitted}}Answer them where they are, not only in your final report. For each thread, say what you did about it and close it:

  {{comment_cmd}}

Drop `--resolve` when you are asking something back instead of settling it. An unresolved thread is delivered again on every later run, so leaving one open is how you hand a question forward.
</followup>"""

_FEATURE_BODY = """<feature>
This run is a session of feature {{feature_named}}; regin attributes it automatically. If you find something the feature got wrong, declare it against that id: `regin defects open "<what>" --feature {{feature_id}} --session <session id>`. Shipping is the operator's act on the feature, not yours.
The feature's spec is the description this task was cut from, and this run is what proves it right or wrong. If you finish knowing the spec now contradicts the code — a decision reversed, a constraint that turned out false, a piece deliberately not built — rewrite it rather than leaving the next run to inherit the stale text:

  {{spec_cmd}}

Revise it in place as the design now stands; do not append a changelog of this run. Leave it alone when the work simply did what it already said.
</feature>"""

_FINISH_BODY = """<finish>
This work was launched from a regin task. Running the command below is the ONLY thing that marks the task complete and releases whatever depends on it; your session stays open either way, so run it as soon as the work is done rather than waiting to be stopped.
{{order}}
   {{finish_cmd}} --reason '<what you did, and why>'
The reason is not bookkeeping: regin posts it on the task as your report, and it is the only account of this run anybody gets without reading the whole session. Say what you actually did, what you decided, and anything you deliberately left — a few sentences, not a transcript.
If you could not finish, run the same command with `--status failed --reason '<what is blocking it>'`. That report stays an open thread and is delivered to the next attempt, so write it for whoever picks this up.
</finish>"""

_FINISH_BASE_UNNAMED_BODY = "the branch this run was based on"

_FOLLOWUP_OMITTED_BODY = (
    "({{dropped}} earlier comment{{plural}} omitted to fit; the most recent "
    "are the ones kept.)"
)

_FINISH_ORDER_LINKED_BODY = (
    "Before you run it: record the work on the linked issue and move its "
    "state — regin re-reads the issue when this command lands, and a state "
    "you change afterwards will not reach the task."
)

_FINISH_ORDER_NATIVE_BODY = (
    "Before you run it: merge this run's work back into {{target}} — that "
    "merge is a precondition of reporting finished, not an optional step, "
    "because whatever depends on this task is released onto that branch and "
    "sees only what has landed there."
)


register_surface(
    LAUNCH_SURFACE_ID,
    label="Task run — launch envelope",
    area="task-run",
    default_body=_LAUNCH_BODY,
    description=(
        "The whole sentence a regin task run is launched with: the armed "
        "skill invocation, the captured idea, then each instruction block in "
        "order. Reorder them here, or add one of your own."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("invocation", "`/<skill> <args>` line when the task was armed with a skill, else empty.", example="/goal-verified\\n\\n", required=False),
        PromptVariable("body", "The task's captured idea (its body, or its title when bodyless), truncated to PROMPT_MAX."),
        PromptVariable("tracker", "Rendered `task-run-tracker` block. Empty for a native feature.", required=False),
        PromptVariable("followup", "Rendered `task-run-followup` block. Empty when no thread is open.", required=False),
        PromptVariable("feature", "Rendered `task-run-feature` block. Empty for a task under no feature.", required=False),
        PromptVariable("finish", "Rendered `task-run-finish` block. Always present."),
    ),
)

register_surface(
    TRACKER_SURFACE_ID,
    label="Task run — tracker block",
    area="task-run",
    default_body=_TRACKER_BODY,
    description=(
        "Told to a run whose feature is tracked in an external issue tracker: "
        "which issue it is, and that the issue — not regin's snapshot — is "
        "the source of truth."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("tracker_name", "Display name of the tracker integration.", example="Linear"),
        PromptVariable("external_ref", "The issue's reference in that tracker.", example="CAI-223"),
        PromptVariable("external_url", "Link to the issue.", required=False),
    ),
)

register_surface(
    FOLLOWUP_SURFACE_ID,
    label="Task run — follow-up comments block",
    area="task-run",
    default_body=_FOLLOWUP_BODY,
    description=(
        "The unresolved comment threads left on this task, and how the run "
        "answers them. Redelivered on every run until somebody resolves the "
        "thread."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("comments", "The rendered comments that fit, oldest first, one `[author] body` line each."),
        PromptVariable("omitted", "Note naming how many older comments were dropped to fit. Empty when none were.", required=False),
        PromptVariable("comment_cmd", "The literal `regin task comment` command, with placeholders for the thread and the reply."),
    ),
)

register_surface(
    FEATURE_SURFACE_ID,
    label="Task run — feature block",
    area="task-run",
    default_body=_FEATURE_BODY,
    description=(
        "Which feature this run counts under: how to blame a defect on it, "
        "and when to rewrite its spec because the work proved the spec wrong."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("feature_named", "The feature as the prompt names it.", example="#80 (Agent SDK runs)"),
        PromptVariable("feature_id", "The feature's numeric id.", example="80"),
        PromptVariable("spec_cmd", "The literal `regin feature spec` command, with this feature's id filled in."),
    ),
)

register_surface(
    FINISH_SURFACE_ID,
    label="Task run — finish block",
    area="task-run",
    default_body=_FINISH_BODY,
    description=(
        "The one instruction that lets a task ever complete: the exact "
        "`task finish` command, and what has to be true before it is run."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("order", "What must happen before finishing — the linked or the native variant."),
        PromptVariable("finish_cmd", "The literal `regin task finish <run id>` command."),
    ),
)

register_surface(
    FOLLOWUP_OMITTED_SURFACE_ID,
    label="Task run — dropped-comments note",
    area="task-run",
    default_body=_FOLLOWUP_OMITTED_BODY,
    description=(
        "Told to a run whose oldest comments would not fit the block's "
        "budget. The agent is told its history is incomplete rather than left "
        "to assume it has all of it."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("dropped", "How many older comments were dropped.", example="2"),
        PromptVariable("plural", "`s` unless exactly one was dropped.", required=False),
    ),
)

register_surface(
    FINISH_BASE_UNNAMED_SURFACE_ID,
    label="Task run — unnamed base branch",
    area="task-run",
    default_body=_FINISH_BASE_UNNAMED_BODY,
    description=(
        "Stands in for `{{target}}` in the native finish precondition when "
        "the run was armed with no base branch: the instruction still holds, "
        "it just cannot name where to merge."
    ),
    applies_to=("external-agent",),
)

register_surface(
    FINISH_ORDER_LINKED_SURFACE_ID,
    label="Task run — finish precondition (tracked feature)",
    area="task-run",
    default_body=_FINISH_ORDER_LINKED_BODY,
    description=(
        "The `{{order}}` line of the finish block when the feature is tracked "
        "externally: record the work on the issue first, because regin "
        "re-reads it at the moment the finish command lands."
    ),
    applies_to=("external-agent",),
)

register_surface(
    FINISH_ORDER_NATIVE_SURFACE_ID,
    label="Task run — finish precondition (native feature)",
    area="task-run",
    default_body=_FINISH_ORDER_NATIVE_BODY,
    description=(
        "The `{{order}}` line of the finish block for a run with no external "
        "issue: merge the slice back first, because dependents are released "
        "onto that branch and see only what has landed there."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("target", "The base branch named, or a generic phrase when the run was armed with none.", example="`master`"),
    ),
)


# The bodies as first ported from the f-strings, before the follow-up block
# learned to ask for a reply and the feature block for a spec rewrite. A stored
# row still hashing to one of these was seeded, never edited, and is healed to
# the current default by `seed_builtin_skeletons`.
register_retired_default(
    FOLLOWUP_SURFACE_ID,
    sha256="217bbcf15f95146ee252082e8b5768f9d3977d15a2f618d21690656e06ea8457")
register_retired_default(
    FEATURE_SURFACE_ID,
    sha256="cf0c2b6f698a75a4ae248e1bde62e13069053b56727f3e7ec7383e91b3a4db52")
# The finish block before it asked for a report rather than only a status.
register_retired_default(
    FINISH_SURFACE_ID,
    sha256="2b4848c1013bcebd4d7d731c4d83989c08996f39515faa4da90762b9c223b2fa")
