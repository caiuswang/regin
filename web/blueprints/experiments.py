"""Experiment CRUD + per-experiment rule-trigger rollups."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlmodel import select

from lib import experiments
from lib.skills import skill_registry, skill_sync
from lib.auth import require_editor
from lib.rules.grit_rule_index import rules_for_guide
from lib.orm import SessionLocal
from lib.orm.models import RuleTrigger


experiments_bp = Blueprint("experiments", __name__)


def _rollup(session, guide_rules, *, since: str, experiment_id):
    """One row: total checks + fired count + distinct sessions for the
    given rule set, scoped by experiment_id (None → baseline)."""
    if not guide_rules:
        return {"sessions": 0, "checks": 0, "fired": 0}
    stmt = (
        select(
            func.count(RuleTrigger.id).label("checks"),
            func.coalesce(func.sum(RuleTrigger.triggered), 0).label("fired"),
            func.count(func.distinct(RuleTrigger.session_id)).label("sessions"),
        )
        .where(
            RuleTrigger.rule_id.in_(guide_rules),
            RuleTrigger.checked_at >= since,
        )
    )
    if experiment_id is None:
        stmt = stmt.where(RuleTrigger.experiment_id.is_(None))
    else:
        stmt = stmt.where(RuleTrigger.experiment_id == experiment_id)
    row = session.exec(stmt).one()
    return {"sessions": row[2] or 0, "checks": row[0] or 0, "fired": row[1] or 0}


# ── Read endpoints ─────────────────────────────────────────────

@experiments_bp.route("/api/experiments")
def api_experiments():
    rows = experiments.list_all()
    by_pattern: dict[str, list[dict]] = {}
    if rows:
        with SessionLocal() as session:
            for r in rows:
                stmt = select(
                    func.count(RuleTrigger.id).label("total"),
                    func.coalesce(func.sum(RuleTrigger.triggered), 0).label("fired"),
                ).where(RuleTrigger.experiment_id == r["id"])
                counts = session.exec(stmt).one()
                r["trigger_total"] = counts[0] or 0
                r["trigger_fired"] = counts[1] or 0
                by_pattern.setdefault(r["pattern_slug"], []).append(r)
    grouped = sorted(by_pattern.items())
    return jsonify({
        "grouped": [[k, v] for k, v in grouped],
        "total": len(rows),
    })


@experiments_bp.route("/api/experiments/<int:experiment_id>")
def api_experiment_detail(experiment_id):
    exp = experiments.get(experiment_id)
    if not exp:
        return jsonify({"error": "not found"}), 404

    guide_rules = [r["id"] for r in rules_for_guide(exp["pattern_slug"])]
    since = exp["created_at"]

    with SessionLocal() as session:
        baseline = _rollup(session, guide_rules, since=since, experiment_id=None)
        experiment_data = _rollup(session, guide_rules, since=since,
                                   experiment_id=experiment_id)

        per_rule = []
        for rid in guide_rules:
            b_stmt = select(
                func.count(RuleTrigger.id),
                func.coalesce(func.sum(RuleTrigger.triggered), 0),
            ).where(
                RuleTrigger.rule_id == rid,
                RuleTrigger.checked_at >= since,
                RuleTrigger.experiment_id.is_(None),
            )
            e_stmt = select(
                func.count(RuleTrigger.id),
                func.coalesce(func.sum(RuleTrigger.triggered), 0),
            ).where(
                RuleTrigger.rule_id == rid,
                RuleTrigger.experiment_id == experiment_id,
            )
            b = session.exec(b_stmt).one()
            e = session.exec(e_stmt).one()
            per_rule.append({
                "rule_id": rid,
                "baseline_checks": b[0] or 0,
                "baseline_fired": b[1] or 0,
                "experiment_checks": e[0] or 0,
                "experiment_fired": e[1] or 0,
            })

    def _rate(d):
        return (d["fired"] / d["checks"]) if d["checks"] else None
    baseline["rate"] = _rate(baseline)
    experiment_data["rate"] = _rate(experiment_data)

    available_sections = experiments.list_sections(exp["pattern_slug"])
    return jsonify({
        "exp": exp,
        "baseline": baseline,
        "experiment": experiment_data,
        "per_rule": per_rule,
        "available_sections": available_sections,
    })


# ── Mutation endpoints ─────────────────────────────────────────

@experiments_bp.route("/api/experiments", methods=["POST"])
@require_editor
def api_create_experiment():
    data = request.get_json(silent=True) or {}
    slug = data.get("pattern_slug", "")
    name = (data.get("name") or "").strip()
    sections = data.get("sections", [])
    if not name or not sections:
        return jsonify({"ok": False, "msg": "name and sections required"})
    try:
        experiments.create(slug, name, sections)
        return jsonify({"ok": True, "msg": f"created experiment '{name}'"})
    except Exception as exc:
        return jsonify({"ok": False, "msg": str(exc)})


@experiments_bp.route("/api/experiments/<int:experiment_id>/edit", methods=["POST"])
@require_editor
def api_edit_experiment(experiment_id):
    exp = experiments.get(experiment_id)
    if not exp:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    sections = data.get("sections", [])
    if not name or not sections:
        return jsonify({"ok": False, "msg": "name and sections required"})
    slug = experiments.update(experiment_id, name, sections)
    deploy_msg = ""
    if slug and exp.get("active"):
        sid = skill_registry.skill_id_for_procedure(slug)
        if sid:
            deploy_msg = skill_sync.push(sid, force=True)
    return jsonify({"ok": True, "msg": f'updated ({deploy_msg or "idle"})'})


@experiments_bp.route("/api/experiments/<int:experiment_id>/activate", methods=["POST"])
@require_editor
def api_activate_experiment(experiment_id):
    slug = experiments.activate(experiment_id)
    if not slug:
        return jsonify({"error": "not found"}), 404
    sid = skill_registry.skill_id_for_procedure(slug)
    deploy_msg = ""
    if sid:
        deploy_msg = skill_sync.push(sid, force=True)
    return jsonify({"ok": True, "msg": f"activated ({deploy_msg})"})


@experiments_bp.route("/api/experiments/<int:experiment_id>/deactivate", methods=["POST"])
@require_editor
def api_deactivate_experiment(experiment_id):
    slug = experiments.deactivate(experiment_id)
    if not slug:
        return jsonify({"error": "not found"}), 404
    sid = skill_registry.skill_id_for_procedure(slug)
    deploy_msg = ""
    if sid:
        deploy_msg = skill_sync.push(sid, force=True)
    return jsonify({"ok": True, "msg": f"deactivated ({deploy_msg})"})


@experiments_bp.route("/api/experiments/<int:experiment_id>/delete", methods=["POST"])
@require_editor
def api_delete_experiment(experiment_id):
    exp = experiments.get(experiment_id)
    if not exp:
        return jsonify({"error": "not found"}), 404
    experiments.delete(experiment_id)
    # If the deleted experiment was active, its concealed body is still on
    # disk. Force-redeploy to restore the full (unconcealed) skill, mirroring
    # what deactivate does — otherwise the agent keeps reading a partially
    # hidden guide with no experiment to explain it.
    deploy_msg = ""
    if exp.get("active"):
        sid = skill_registry.skill_id_for_procedure(exp["pattern_slug"])
        if sid:
            try:
                deploy_msg = skill_sync.push(sid, force=True)
            except Exception as exc:
                deploy_msg = f"redeploy failed: {exc}"
    return jsonify({
        "ok": True,
        "msg": f'deleted experiment {exp["name"]} ({deploy_msg or "idle"})',
    })
