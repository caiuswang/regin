"""Attribution by an external agent, for a defect whose feature nobody can find.

A defect with no feature cannot get a fix task — the task would be an orphan —
so a report the operator cannot place used to stop dead. This asks the
configured external agent to choose from the live features and writes its pick
through `store.attribute`, the same verb a human uses: dedup clashes, blame
binding and fix-task refresh all apply unchanged.

The agent's answer is only ever an id from the candidate list it was shown.
Anything else — an id it invented, an abandoned feature, prose, null — is a
refusal and writes nothing, so a confused agent leaves the defect exactly as
unattributed as it found it. The pick is an operator-triggered act, never a
background sweep, and stays re-attributable like any other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.defects import fix_items, store
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.sync import Repo

log = get_activity_logger("defects")

_SPEC_LEAD = 160


@dataclass(frozen=True)
class Attribution:
    defect: Defect
    reason: str
    #: Why the requested fix task was not filed. The attribution stands
    #: either way — it is already committed, so it is reported, not undone.
    fix_error: Optional[str] = None


def _llm():
    from lib.grader.adapters import ExternalAgentJudge  # noqa: PLC0415

    judge = ExternalAgentJudge()
    return judge if judge.selected_agent is not None else None


def candidates() -> list[Feature]:
    from lib.features.index import LIVE_SQL  # noqa: PLC0415

    with SessionLocal() as session:
        return list(session.exec(
            select(Feature).where(text(LIVE_SQL)).order_by(Feature.id)).all())


def _feature_line(feature: Feature, repos: dict[int, str]) -> str:
    lead = " ".join((feature.body or "").split())[:_SPEC_LEAD]
    project = repos.get(feature.project_id, "unfiled")
    return f"F{feature.id} | {feature.title} | project: {project} | {lead}"


def build_prompt(row: Defect, features: list[Feature]) -> str:
    from lib.prompts.resolve import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.defects import (  # noqa: PLC0415
        AUTO_ATTRIBUTE_SURFACE_ID)

    with SessionLocal() as session:
        repos = {r.id: r.name for r in session.exec(select(Repo)).all()}
    paths = json.loads(row.suspect_paths_json) if row.suspect_paths_json else []
    return render_surface(AUTO_ATTRIBUTE_SURFACE_ID, {
        "symptom": row.symptom,
        "suspect_paths": ", ".join(paths) or "none recorded",
        "features": "\n".join(_feature_line(f, repos) for f in features),
    })


def _objects(answer: str):
    decoder = json.JSONDecoder()
    for start in reversed([i for i, ch in enumerate(answer) if ch == "{"]):
        try:
            yield decoder.raw_decode(answer, start)[0]
        except ValueError:
            continue


def parse_answer(answer: str) -> tuple[Optional[int], str]:
    """The last JSON object naming `feature_id`, as `(feature_id, reason)`."""
    for parsed in _objects(answer or ""):
        if not isinstance(parsed, dict) or "feature_id" not in parsed:
            continue
        return (_as_feature_id(parsed.get("feature_id")),
                str(parsed.get("reason") or "").strip())
    return None, ""


def _as_feature_id(value) -> Optional[int]:
    if isinstance(value, str):
        value = value.strip().lstrip("Ff")
        return int(value) if value.isdigit() else None
    # `isinstance(True, int)` — a bool here would name feature 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _choose(row: Defect) -> tuple[int, str]:
    llm = _llm()
    if llm is None:
        raise store.DefectError(
            "no external agent is configured to attribute with — add one "
            "under `topic_proposal_external_agents`, or pick the feature by hand")
    features = candidates()
    if not features:
        raise store.DefectError("there is no live feature to attribute to")
    answer = llm.complete(build_prompt(row, features))
    if answer is None:
        raise store.DefectError(
            "the external agent did not answer — pick the feature by hand")
    feature_id, reason = parse_answer(answer)
    if feature_id is None:
        raise store.DefectError(
            "the agent could not place this defect"
            + (f" ({reason})" if reason else "")
            + " — pick the feature by hand")
    if feature_id not in {f.id for f in features}:
        raise store.DefectError(
            f"the agent named feature {feature_id}, which is not a live "
            "feature — pick the feature by hand")
    return feature_id, reason


def auto_attribute(defect_id: int, *, fix_task: bool = False) -> Attribution:
    """Let the agent name the feature, write it, and optionally file the fix
    task — the step the missing feature was blocking."""
    row = store.get(defect_id)
    if row.feature_id is not None:
        raise store.DefectError(
            f"#{row.id} is already attributed to feature {row.feature_id} — "
            f"re-attribute by hand if that is wrong")
    feature_id, reason = _choose(row)
    # The agent may have taken minutes; a human who attributed meanwhile wins.
    row = store.attribute(row.id, feature_id, only_if_unattributed=True)
    log.write("defect_auto_attributed", defect_id=row.id,
              feature_id=feature_id, reason=reason)
    if not fix_task:
        return Attribution(defect=row, reason=reason)
    try:
        row = fix_items.create_for(row.id)
    except store.DefectError as exc:
        return Attribution(defect=row, reason=reason, fix_error=str(exc))
    return Attribution(defect=row, reason=reason)


__all__ = ["Attribution", "auto_attribute", "build_prompt", "candidates",
           "parse_answer"]
