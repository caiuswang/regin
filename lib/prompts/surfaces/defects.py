"""The body of a task cut from a defect — what its run is told the bug is.

The task's body is the ``{{body}}`` of ``task-run-launch``, so this is the
first thing the fix run's agent reads. ``details`` is pre-rendered because a
line that does not apply (an open defect has no named check) must be absent,
not present and blank: an empty ``missing check:`` reads as one that was named
and left empty. The raw values ride alongside for an operator who wants to
lay them out differently.
"""

from __future__ import annotations

from lib.prompts.registry import PromptVariable, register_surface

FIX_TASK_SURFACE_ID = "defect-fix-task"

_FIX_TASK_BODY = """Fix regin defect #{{defect_id}}, reported against feature {{feature_named}}:

{{symptom}}
{{images}}{{details}}"""


register_surface(
    FIX_TASK_SURFACE_ID,
    label="Defect — fix task body",
    area="defect",
    default_body=_FIX_TASK_BODY,
    description=(
        "The body of the task created by a defect's \"Create fix task\" "
        "button (and `regin defects fix-item --create`). A task run hands it "
        "to the agent as the captured idea."
    ),
    applies_to=("external-agent",),
    variables=(
        PromptVariable("defect_id", "The defect's id.", example="42"),
        PromptVariable("symptom", "The symptom, verbatim as reported."),
        PromptVariable("feature_named", "`#<id> (<title>)` of the feature the defect is blamed on."),
        PromptVariable("round", "Fix attempts on this defect so far (1 until it is reopened).", example="1"),
        PromptVariable("images", "One `![](image:N)` line per screenshot the symptom does not already reference, each followed by a newline. Empty when there are none.", required=False),
        PromptVariable("details", "One line each, only those that apply: `reported in session …`, `suspect paths: …`, `missing check: …`.", required=False),
        PromptVariable("reported_session", "The reporting session id. Empty when none was recorded.", required=False),
        PromptVariable("suspect_paths", "Comma-separated suspect paths. Empty when none.", required=False),
        PromptVariable("missing_check", "The check named at close. Empty on an open defect.", required=False),
    ),
)
