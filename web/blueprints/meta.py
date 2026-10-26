"""Top-level 'meta' endpoints used by the sidebar + landing views.

These are the generic cross-cutting reads the Vue SPA wires into the
sidebar/status UI: the provider list, a repo status summary, and a
doctor/self-test.
None of them fit cleanly into one of the domain blueprints (auth /
patterns / skills / trace / …), so they live together here.
"""

from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import func
from sqlmodel import select

from lib.doctor import run_checks
from lib.providers import active_provider_id, active_provider_skill_paths, provider_capability_rows
from lib.orm import SessionLocal
from lib.orm.models import Branch, PatternDeployment, PatternDoc, Repo


meta_bp = Blueprint("meta", __name__)


@meta_bp.route("/api/providers")
def api_providers():
    return jsonify({
        "active_provider": active_provider_id(),
        "providers": provider_capability_rows(),
        "skill_paths": active_provider_skill_paths(),
    })


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
