"""A feature's spec and build steps, created from the feature's own verbs.

The same shape as a defect's fix task (`lib/defects/fix_items.py`): the key
`feature:<id>:<kind>` makes it one step of each kind per feature, so a second
click, a second tab or the CLI after the browser all resolve to the first row.
A second attempt is another run of that row, never another row.

The title is a derived label — the feature's title has one home, `features` —
and the spec is never copied into the body: the body is only the operator's
optional instruction for this step.
"""

from __future__ import annotations

from typing import Optional

from lib.features import store as feature_store
from lib.orm.models.tasks import TASK_KIND_BUILD, TASK_KIND_SPEC, TITLE_MAX

SOURCE = "feature"
VERB_KINDS: tuple[str, ...] = (TASK_KIND_SPEC, TASK_KIND_BUILD)
_LABEL = {TASK_KIND_SPEC: "Spec", TASK_KIND_BUILD: "Build"}


class StepRejected(ValueError):
    """A kind the feature verbs do not create — a 400 naming `kind`."""

    field = "kind"


def external_id(feature_id: int, kind: str) -> str:
    return f"feature:{int(feature_id)}:{kind}"


def open_step(feature_id: int, kind: str,
              body: Optional[str] = None) -> tuple[dict, bool]:
    """The feature's step of this kind, created on first use.

    Returns `(task, created)`. Raises `FeatureError` for an unknown feature
    and `StepRejected` for a kind other than spec or build.
    """
    from lib.tasks import store as task_store  # noqa: PLC0415

    if kind not in VERB_KINDS:
        raise StepRejected(
            f"kind must be one of {', '.join(VERB_KINDS)}")
    feature = feature_store.require(feature_id)
    title = f"{_LABEL[kind]} · {feature.title}"[:TITLE_MAX]
    return task_store.capture_task(
        title=title, body=(body or "").strip() or None, source=SOURCE,
        external_id=external_id(feature_id, kind), feature_id=feature_id,
        kind=kind)
