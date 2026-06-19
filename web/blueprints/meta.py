"""Top-level 'meta' endpoints used by the sidebar + landing views.

These are the generic cross-cutting reads the Vue SPA wires into the
sidebar/status UI: a repo status summary, a dashboard stats bundle, a
full-text search shim, a doctor/self-test, and a per-repo detail view.
None of them fit cleanly into one of the domain blueprints (auth /
patterns / skills / trace / …), so they live together here.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_
from sqlmodel import select

from lib import rule_engines
from lib.skills import skill_registry, skill_sync as _ss
from lib.doctor import run_checks
from lib.rules.grit_rule_index import load_rules_index
from lib.providers import active_provider_id, active_provider_skill_paths, provider_capability_rows
from lib.orm import SessionLocal
from lib.orm.models import (
    Branch, PatternDeployment, PatternDoc, Repo, RuleTrigger,
    Session as SessionModel, Tag,
)
from lib.search import search_patterns


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


@meta_bp.route("/api/dashboard")
def api_dashboard():
    with SessionLocal() as session:
        # Repo × tracked branch + pattern_count per repo — single query.
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

        # Stats bundle.
        total_repos = session.exec(
            select(func.count(Repo.id)).where(Repo.is_active == 1)
        ).one()
        total_patterns = session.exec(select(func.count(PatternDoc.id))).one()
        total_tags = session.exec(select(func.count(Tag.id))).one()
        trigger_row = session.exec(
            select(
                func.count(RuleTrigger.id).label("total"),
                func.coalesce(func.sum(RuleTrigger.triggered), 0).label("fired"),
            )
        ).one()

    skill_counts = {"total": 0, "in_sync": 0, "drifted": 0,
                     "source_only": 0, "project_only": 0, "deployed_only": 0}
    for _sid, _stype, _src, _dep, sstate in _ss.list_states():
        skill_counts["total"] += 1
        if sstate in skill_counts:
            skill_counts[sstate] += 1

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
    stats = {
        "total_repos": total_repos,
        "total_patterns": total_patterns,
        "total_tags": total_tags,
        "skills": skill_counts,
        "rules": {
            "total": len(severities),
            "error": sum(1 for s in severities if s == "error"),
            "warn": sum(1 for s in severities if s == "warn"),
            "triggers": trigger_row[0],
            "fired": trigger_row[1],
        },
    }
    return jsonify({"repos": repos, "stats": stats})


_QUICKSEARCH_LIMIT_PER_GROUP = 6


def _quicksearch_patterns(q: str) -> list[dict]:
    items: list[dict] = []
    try:
        for p in search_patterns(q)[:_QUICKSEARCH_LIMIT_PER_GROUP]:
            items.append({
                "title": p.get("title") or p.get("slug"),
                "subtitle": p.get("category") or "",
                "href": f"/patterns/{p['slug']}",
            })
    except Exception:
        return []
    return items


def _quicksearch_skills(q_lower: str) -> list[dict]:
    items: list[dict] = []
    try:
        for sid in skill_registry.all_ids():
            if q_lower not in sid.lower():
                continue
            entry = skill_registry.get(sid) or {}
            if entry.get("type") == "pattern":
                href = f"/patterns/{entry.get('procedure_id', sid)}"
            else:
                href = f"/skills/{sid}"
            items.append({
                "title": sid,
                "subtitle": entry.get("type", "skill"),
                "href": href,
            })
            if len(items) >= _QUICKSEARCH_LIMIT_PER_GROUP:
                break
    except Exception:
        return []
    return items


def _quicksearch_sessions(q: str) -> list[dict]:
    items: list[dict] = []
    try:
        with SessionLocal() as session:
            stmt = (
                select(SessionModel)
                .where(or_(
                    SessionModel.trace_id.contains(q),
                    SessionModel.title.contains(q),
                ))
                .order_by(SessionModel.last_seen.desc())
                .limit(_QUICKSEARCH_LIMIT_PER_GROUP)
            )
            for s in session.exec(stmt).all():
                day = (s.last_seen or "").split("T", 1)[0]
                items.append({
                    "title": s.title or s.trace_id[:12],
                    "subtitle": " · ".join(filter(None, [f"{s.span_count} spans", day])),
                    "href": f"/trace/sessions/{s.trace_id}",
                })
    except Exception:
        return []
    return items


def _quicksearch_rules(q_lower: str) -> list[dict]:
    items: list[dict] = []
    try:
        for r in load_rules_index().get("rules", []):
            rule_id = r.get("id") or ""
            guide = r.get("guide") or ""
            if q_lower in rule_id.lower() or q_lower in guide.lower():
                items.append({
                    "title": rule_id,
                    "subtitle": guide,
                    "href": f"/rules/{rule_id}",
                })
                if len(items) >= _QUICKSEARCH_LIMIT_PER_GROUP:
                    break
    except Exception:
        return []
    return items


@meta_bp.route("/api/quicksearch")
def api_quicksearch():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"query": "", "groups": []})

    q_lower = q.lower()
    candidates = [
        ("Patterns", "patterns", _quicksearch_patterns(q)),
        ("Skills", "skills", _quicksearch_skills(q_lower)),
        ("Sessions", "trace", _quicksearch_sessions(q)),
        ("Rules", "rules", _quicksearch_rules(q_lower)),
    ]
    groups = [
        {"label": label, "icon": icon, "items": items}
        for label, icon, items in candidates if items
    ]
    return jsonify({"query": q, "groups": groups})


# /api/repos/<name> moved to web/blueprints/repos.py alongside the
# add/remove endpoints. The remaining helpers above (_repo_to_dict,
# _branch_to_dict, _pattern_to_dict) are still used by /api/dashboard.
