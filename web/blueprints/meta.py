"""Top-level 'meta' endpoints used by the sidebar + landing views.

These are the generic cross-cutting reads the Vue SPA wires into the
sidebar/status UI: a repo status summary, a dashboard stats bundle, a
full-text search shim, a doctor/self-test, and a per-repo detail view.
None of them fit cleanly into one of the domain blueprints (auth /
patterns / skills / trace / …), so they live together here.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlmodel import select

from lib import rule_engines
from lib.skills import skill_sync as _ss
from lib.doctor import run_checks
from lib.rules.grit_rule_index import load_rules_index
from lib.providers import active_provider_id, active_provider_skill_paths, provider_capability_rows
from lib.orm import SessionLocal
from lib.orm.models import (
    Branch, PatternDeployment, PatternDoc, Repo, RuleTrigger,
    Tag,
)


meta_bp = Blueprint("meta", __name__)


@meta_bp.route("/api/providers")
def api_providers():
    return jsonify({
        "active_provider": active_provider_id(),
        "providers": provider_capability_rows(),
        "skill_paths": active_provider_skill_paths(),
    })


def _repo_to_dict(r: Repo) -> dict:
    return {
        "id": r.id, "name": r.name, "path": r.path,
        "description": r.description, "is_active": r.is_active,
        "default_branch": r.default_branch,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def _branch_to_dict(b: Branch) -> dict:
    return {
        "id": b.id, "repo_id": b.repo_id, "name": b.name,
        "is_tracked": b.is_tracked, "created_at": b.created_at,
    }


def _pattern_to_dict(pd: PatternDoc) -> dict:
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "file_path": pd.file_path, "category": pd.category,
        "content_hash": pd.content_hash,
        "created_at": pd.created_at, "updated_at": pd.updated_at,
    }


@meta_bp.route("/api/status")
def api_status():
    """Repo status summary: repo × branch × pattern count per repo."""
    with SessionLocal() as session:
        stmt = (
            select(
                Repo.name.label("name"),
                Branch.name.label("branch"),
                select(func.count(func.distinct(PatternDoc.id)))
                    .select_from(PatternDeployment)
                    .join(PatternDoc, PatternDoc.slug == PatternDeployment.pattern_slug)
                    .where(PatternDeployment.scope == "project")
                    .where(PatternDeployment.project_id == Repo.id)
                    .scalar_subquery().label("patterns"),
            )
            .outerjoin(Branch, (Branch.repo_id == Repo.id) & (Branch.is_tracked == 1))
            .where(Repo.is_active == 1)
            .order_by(Repo.name)
        )
        return jsonify([dict(r._mapping) for r in session.exec(stmt).all()])


@meta_bp.route("/api/doctor")
def api_doctor():
    return jsonify(run_checks())


def _dashboard_repos(session) -> list:
    """Repo × tracked branch + pattern_count per repo — single query."""
    repo_stmt = (
        select(
            Repo, Branch,
            func.count(func.distinct(PatternDoc.id)).label("pattern_count"),
        )
        .outerjoin(Branch, (Branch.repo_id == Repo.id) & (Branch.is_tracked == 1))
        .outerjoin(PatternDeployment,
                   (PatternDeployment.project_id == Repo.id)
                   & (PatternDeployment.scope == "project"))
        .outerjoin(PatternDoc, PatternDoc.slug == PatternDeployment.pattern_slug)
        .where(Repo.is_active == 1)
        .group_by(Repo.id)
        .order_by(Repo.name)
    )
    repos = []
    for r, b, count in session.exec(repo_stmt).all():
        d = _repo_to_dict(r)
        d["branch_name"] = b.name if b else None
        d["pattern_count"] = count
        repos.append(d)
    return repos


def _dashboard_totals(session) -> dict:
    return {
        "total_repos": session.exec(
            select(func.count(Repo.id)).where(Repo.is_active == 1)
        ).one(),
        "total_patterns": session.exec(select(func.count(PatternDoc.id))).one(),
        "total_tags": session.exec(select(func.count(Tag.id))).one(),
        "trigger_row": session.exec(
            select(
                func.count(RuleTrigger.id).label("total"),
                func.coalesce(func.sum(RuleTrigger.triggered), 0).label("fired"),
            )
        ).one(),
    }


def _skill_state_counts() -> dict:
    skill_counts = {"total": 0, "in_sync": 0, "drifted": 0,
                     "source_only": 0, "project_only": 0, "deployed_only": 0}
    for _sid, _stype, _src, _dep, sstate in _ss.list_states():
        skill_counts["total"] += 1
        if sstate in skill_counts:
            skill_counts[sstate] += 1
    return skill_counts


def _rule_severities() -> list:
    grit_rules = load_rules_index().get("rules", [])
    seen_ids = {r.get("id") for r in grit_rules}
    severities = [r.get("severity") for r in grit_rules]
    for engine in rule_engines.all_engines():
        if getattr(engine, "kind", "") == "grit":
            continue
        for rule in engine.parse_rules():
            if rule.id in seen_ids:
                continue
            seen_ids.add(rule.id)
            severities.append(rule.severity)
    return severities


def _rules_stats(severities: list, trigger_row) -> dict:
    return {
        "total": len(severities),
        "error": sum(1 for s in severities if s == "error"),
        "warn": sum(1 for s in severities if s == "warn"),
        "triggers": trigger_row[0],
        "fired": trigger_row[1],
    }


@meta_bp.route("/api/dashboard")
def api_dashboard():
    with SessionLocal() as session:
        repos = _dashboard_repos(session)
        totals = _dashboard_totals(session)

    skill_counts = _skill_state_counts()
    severities = _rule_severities()
    stats = {
        "total_repos": totals["total_repos"],
        "total_patterns": totals["total_patterns"],
        "total_tags": totals["total_tags"],
        "skills": skill_counts,
        "rules": _rules_stats(severities, totals["trigger_row"]),
    }
    return jsonify({"repos": repos, "stats": stats})


# /api/repos/<name> moved to web/blueprints/repos.py alongside the
# add/remove endpoints. The remaining helpers above (_repo_to_dict,
# _branch_to_dict, _pattern_to_dict) are still used by /api/dashboard.
