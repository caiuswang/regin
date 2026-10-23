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
FEATURE_IMAGES_SURFACE_ID = "task-run-feature-images"
FINISH_SURFACE_ID = "task-run-finish"
FINISH_ORDER_LINKED_SURFACE_ID = "task-run-finish-order-linked"
FINISH_ORDER_NATIVE_SURFACE_ID = "task-run-finish-order-native"
FINISH_BASE_UNNAMED_SURFACE_ID = "task-run-finish-base-unnamed"
FINISH_ORDER_DEFECTS_SURFACE_ID = "task-run-finish-order-defects"
FOLLOWUP_OMITTED_SURFACE_ID = "task-run-followup-omitted"
SPEC_SURFACE_ID = "task-run-spec"
REFERENCES_SURFACE_ID = "task-run-references"
LANGUAGE_SURFACE_ID = "task-run-language"

# NOTE: tests/prompts/test_task_run_parity.py asserts `runs._prompt` == a
# frozen reference builder, so the port stays provable against the f-strings it
# replaced. Edit the wording here and that reference together.
_LAUNCH_BODY = (
    "{{invocation}}{{body}}{{tracker}}{{followup}}{{feature}}{{spec}}"
    "{{references}}{{finish}}{{language}}"
)

_LANGUAGE_BODY = """<language>
Write everything meant for a person in {{language}}: your replies, send_to_user messages, any spec or report you write, task comments, and the `--reason` of the finish command. Leave code, identifiers, commands, file paths and quoted output exactly as they are.
</language>"""

_REFERENCES_BODY = """<references>
What the task and its feature's spec cite, looked up when this run was launched: a title or state below is current, where the label written into the link may be stale. These are pointers, not copies — before you rely on one, read it with the command on its line.
{{lines}}
</references>"""

_SPEC_BODY = """<spec>
This run is the spec step of feature {{feature_named}}. Its deliverable is the feature's spec, written onto the feature row — nothing else. Write the spec to a file, then pass that file's path (without `--file` the command prints the current spec):

  {{spec_cmd}}

Change no code, and do not start the build: read what you need, write the spec (intent, shape, rules, acceptance items that can be checked), and finish. The operator approves the spec before any build step runs, so end by running the finish command below rather than going on to implement it.
</spec>"""

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
The feature's spec is the description this task was cut from, and this run is what proves it right or wrong. If you finish knowing the spec now contradicts the code — a decision reversed, a constraint that turned out false, a piece deliberately not built — rewrite it rather than leaving the next run to inherit the stale text. Read the current spec by running the command below without `--file`; write the revision to a file, then pass that file's path:

  {{spec_cmd}}

Revise it in place as the design now stands; do not append a changelog of this run. Leave it alone when the work simply did what it already said.{{images}}
</feature>"""

_FEATURE_IMAGES_BODY = """The spec cites the feature's images as `![](image:N)` (the feature holds: {{indices}}). They are NOT attached to this prompt — when the work needs one, fetch it; the command writes the image to a file and prints the path, which you then read:

  {{image_cmd}}"""

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
        PromptVariable("invocation", "`/<skill> <args>` line when the task was armed with a skill (plus a line naming any further armed skills), else empty.", example="/goal-verified\\n\\n", required=False),
        PromptVariable("body", "The task's captured idea (its body, or its title when bodyless), truncated to PROMPT_MAX."),
        PromptVariable("tracker", "Rendered `task-run-tracker` block. Empty for a native feature.", required=False),
        PromptVariable("followup", "Rendered `task-run-followup` block. Empty when no thread is open.", required=False),
        PromptVariable("feature", "Rendered `task-run-feature` block. Empty for a task under no feature.", required=False),
        PromptVariable("spec", "Rendered `task-run-spec` block. Present only when the task is a feature's spec step.", required=False),
        PromptVariable("references", "Rendered `task-run-references` block. Empty when neither the task body nor its feature's spec cites anything.", required=False),
        PromptVariable("finish", "Rendered `task-run-finish` block. Always present."),
        PromptVariable("language", "Rendered `task-run-language` block. Empty when neither the project nor the global default names an output language.", required=False),
    ),
)

register_surface(
    LANGUAGE_SURFACE_ID,
    label="Task run — output language block",
    area="task-run",
    default_body=_LANGUAGE_BODY,
    description=(
        "The language a run writes its human-facing text in: the project's "
        "own output language, else `settings.tasks.output_language`. Never "
        "rendered when neither is set."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("language", "The output language as the operator typed it.", example="简体中文"),
    ),
)

register_surface(
    REFERENCES_SURFACE_ID,
    label="Task run — references block",
    area="task-run",
    default_body=_REFERENCES_BODY,
    description=(
        "Every @-reference in the task body and its feature's spec, resolved "
        "at launch: one line each with its live title and state, and the "
        "command that reads it in full. A pointer rather than a copy, so it "
        "cannot drift from what it names."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("lines", "One `- …` line per distinct reference, body first, then spec, in order of first appearance; at most 20, then `… and N more references`."),
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
        PromptVariable("spec_cmd", "The literal `regin feature spec` write command, with this feature's id filled in and a placeholder for the spec file."),
        PromptVariable("images", "How to fetch the feature's images. Empty when it has none.", required=False),
    ),
)

register_surface(
    FEATURE_IMAGES_SURFACE_ID,
    label="Task run — feature images line",
    area="task-run",
    default_body=_FEATURE_IMAGES_BODY,
    description=(
        "Inside the feature block: the feature's images are not attached to "
        "the launch, so this names how to fetch one the spec cites."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("indices", "The feature's image numbers, comma-separated.", example="1, 2, 14"),
        PromptVariable("image_cmd", "The literal `regin feature image` command, with this feature's id filled in and `<N>` for the image."),
    ),
)

register_surface(
    SPEC_SURFACE_ID,
    label="Task run — spec step block",
    area="task-run",
    default_body=_SPEC_BODY,
    description=(
        "Told only to a run of a feature's spec step (kind=spec): the "
        "deliverable is the spec on the feature row, no code changes, and the "
        "run ends by finishing so the operator can approve it."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("feature_named", "The feature as the prompt names it.", example="#80 (Agent SDK runs)"),
        PromptVariable("spec_cmd", "The literal `regin feature spec` write command, with this feature's id filled in and a placeholder for the spec file."),
    ),
)
_FINISH_ORDER_DEFECTS_BODY = (
    "This task was cut to fix {{defects}}. Finishing the task does not "
    "close a defect, and `task finish` is refused as done while one it fixes "
    "is still open. Once the fix is verified, close each, naming the check "
    "that would have caught it:\n{{close_cmds}}\nIf it is not fixed, leave "
    "it open and finish with `--status failed` instead."
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
        PromptVariable("finish_cmd", "The literal `regin task finish <task id> --run <run id>` command."),
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

register_surface(
    FINISH_ORDER_DEFECTS_SURFACE_ID,
    label="Task run — finish precondition (open defects)",
    area="task-run",
    default_body=_FINISH_ORDER_DEFECTS_BODY,
    description=(
        "Appended to the `{{order}}` line when open defects point at the "
        "task as their fix item: close each before finishing, because a done "
        "finish is refused while any is still open."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("defects", "The open defects, as `defect #N` / `defects #N, #M`.", example="defect #179"),
        PromptVariable("close_cmds", "One literal `regin defects close <id> --check …` command per defect, indented, one per line."),
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
# The feature block before it named how to fetch the feature's images, which
# stopped riding on the launch.
register_retired_default(
    FEATURE_SURFACE_ID,
    sha256="69808dd3a8401488a5bf603ad0c84df2bc1ab3f8e83a7eb735aabfc5f6382b9d")
# The finish block before it asked for a report rather than only a status.
register_retired_default(
    FINISH_SURFACE_ID,
    sha256="2b4848c1013bcebd4d7d731c4d83989c08996f39515faa4da90762b9c223b2fa")
# The envelope before it carried the spec step's block.
register_retired_default(
    LAUNCH_SURFACE_ID,
    sha256="6e8b6bae19adb35e5d14bfb4486d84eb3b40d593b8812a7f62a724140040388a")
# The envelope before it carried the references block.
register_retired_default(
    LAUNCH_SURFACE_ID,
    sha256="7cdf66128594b8ff971201061cd752fe550e865167fafe002fa481d5468eea0e")
# The feature and spec-step blocks before the spec command named a file: as
# `--file -` it was run bare, with nothing piped in.
register_retired_default(
    FEATURE_SURFACE_ID,
    sha256="e167cff65324376c36a626951828aba3b6350c386f293f04232a6e749adc4709")
register_retired_default(
    SPEC_SURFACE_ID,
    sha256="2a6908d2c096a945b4fd06107a1bc39638562101a9544f6c2202b41a85dfe0c2")
# The envelope before it carried the output-language block.
register_retired_default(
    LAUNCH_SURFACE_ID,
    sha256="1d2090dbc437fdd497ce79406810088571c182c5f01560d4fc1bb207cef120a5")
