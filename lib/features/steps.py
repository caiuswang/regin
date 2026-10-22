"""A feature's steps, one per stage of its project's stage list.

The same shape as a defect's fix task (`lib/defects/fix_items.py`): the key
`feature:<id>:<stage key>` makes it one step per stage per feature, so a
second click, a second tab or the CLI after the browser all resolve to the
first row. A second attempt is another run of that row, never another row.
The default stages keep the keys `spec` and `build`, which is why step tasks
opened before stages existed still match.

The stage's `role` becomes the task's `kind` — what the ledger and the run
read — and its key lands in `stage_key`, so a custom stage needs no new kind.

The title cites the feature as an `@` reference rather than copying its name
— the feature's title has one home, `features` — so a rename reads through on
every surface that draws task titles. The spec is never copied into the body:
the body is only the operator's optional instruction for this step.
"""

from __future__ import annotations

from typing import Optional

from lib.features import stages
from lib.features import store as feature_store
from lib.refs.grammar import format_ref

SOURCE = "feature"


class StepRejected(ValueError):
    """A stage the feature's project does not offer, or a body that is not a
    task body — a 400 naming the field."""

    def __init__(self, message: str, field: str = "stage") -> None:
        super().__init__(message)
        self.field = field


def external_id(feature_id: int, stage_key: str) -> str:
    return f"feature:{int(feature_id)}:{stage_key}"


def open_step(feature_id: int, stage_key,
              body: Optional[str] = None) -> tuple[dict, bool]:
    """The feature's step for this stage, created on first use.

    Returns `(task, created)`. Raises `FeatureError` for an unknown feature
    and `StepRejected` for a key that is not a live stage of the feature's
    project (the global defaults when it has none).
    """
    from lib.orm.models.tasks import BODY_MAX  # noqa: PLC0415
    from lib.tasks import store as task_store  # noqa: PLC0415

    if body is not None and (not isinstance(body, str)
                             or len(body) > BODY_MAX):
        raise StepRejected(f"body must be text of at most {BODY_MAX} "
                           "characters", "body")
    feature = feature_store.require(feature_id)
    stage = (stages.resolve(feature.project_id, stage_key)
             if isinstance(stage_key, str) else None)
    if stage is None:
        live = stages.list_stages(feature.project_id, include_archived=False)
        raise StepRejected("stage must be one of "
                           + ", ".join(s["key"] for s in live["stages"]))
    title = (f"{stage['label']} · "
             f"{format_ref('feature', str(feature.id), feature.title)}")
    return task_store.capture_task(
        title=title, body=(body or "").strip() or None, source=SOURCE,
        external_id=external_id(feature_id, stage["key"]),
        feature_id=feature_id, kind=stage["role"], stage_key=stage["key"])
