"""The body of a task cut from a defect — what its run is told the bug is.

The task's body is the ``{{body}}`` of ``task-run-launch``, so this is the
first thing the fix run's agent reads. The default cites the defect and its
feature as ``[label](kind:key)`` references rather than copying their text, so
a symptom edited or a feature renamed later reaches the run through its
``<references>`` block instead of leaving the task stale. ``details`` is
pre-rendered because a line that does not apply (an open defect has no named
check) must be absent, not present and blank: an empty ``missing check:``
reads as one that was named and left empty. The raw values ride alongside for
an operator who wants to lay them out differently — copying them freezes them
at the moment the task was cut.
"""

from __future__ import annotations

from lib.prompts.registry import (
    PromptVariable, register_retired_default, register_surface)

FIX_TASK_SURFACE_ID = "defect-fix-task"

_FIX_TASK_BODY = """Fix regin {{defects_named}}:
{{defect_refs}}
Reported against feature {{feature_ref}}."""


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
        PromptVariable("defects_named", "`defect` for a task cut from one defect, `defects` for several.", example="defects"),
        PromptVariable("defect_refs", "Every defect the task fixes as an @-reference, one `- [D<id> <symptom>](defect:<id>)` line each, lowest id first — resolved live wherever the body is shown or run.", example="- [D42 the gutter folds](defect:42)\n- [D43 the badge overlaps](defect:43)"),
        PromptVariable("defect_id", "The lowest defect's id.", example="42"),
        PromptVariable("defect_ref", "The lowest defect as an @-reference, `[D<id> <symptom>](defect:<id>)`.", example="[D42 the gutter folds](defect:42)"),
        PromptVariable("feature_ref", "The blamed feature as an @-reference, `[F<id> <title>](feature:<id>)`.", example="[F80 Agent SDK runs](feature:80)"),
        PromptVariable("symptom", "The symptom, verbatim as reported — a copy taken when the task is cut.", required=False),
        PromptVariable("feature_named", "`#<id> (<title>)` of the feature the defect is blamed on — a copy taken when the task is cut.", required=False),
        PromptVariable("round", "Fix attempts on this defect so far (1 until it is reopened).", example="1"),
        PromptVariable("details", "One line each, only those that apply: `reported in session …`, `suspect paths: …`, `missing check: …`.", required=False),
        PromptVariable("reported_session", "The reporting session id. Empty when none was recorded.", required=False),
        PromptVariable("suspect_paths", "Comma-separated suspect paths. Empty when none.", required=False),
        PromptVariable("missing_check", "The check named at close. Empty on an open defect.", required=False),
    ),
)


# Earlier defaults, each describing only the lowest defect of a batch: the
# symptom copied in, then cited as one reference with its screenshots copied
# into the task. A stored row still hashing to one was seeded, never edited.
for _sha in (
        "96692f459d53f969a951414ccb5a70f10202895fa65ae9b1d971537fac207e8a",
        "79332799a7cea8f6d9e4deed5f0a1d73f9682c3d202239c7ce34838ad4248315",
        "143dff4206367c8a9127b6adb64268e59603e333905fa0e772e1ad661d7b4cef"):
    register_retired_default(FIX_TASK_SURFACE_ID, sha256=_sha)
