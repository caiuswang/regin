"""Pattern endpoints split by purpose."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime, timezone

import yaml
from flask import abort, jsonify, make_response, request
from sqlalchemy import func
from sqlmodel import select

from lib import rule_engines
from lib.settings import settings
from lib.auth import get_current_user, require_editor
from web.blueprints import patterns as _pkg
from lib.rules import engine_rule_disable, grit_rule_index
from lib.rules.grit_rule_index import RULES_MD_PATH, rules_for_guide
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDeployment, PatternDoc, Tag

from lib import audit, experiments
from lib.patterns import pattern_deployments
from lib.skills import skill_registry, skill_sync

from web.blueprints.patterns import patterns_bp
from web.blueprints.patterns._helpers import (
    _get_pattern_or_404, _pattern_to_dict, _now_iso,
    _attached_rule_bundles_for_pattern,
)


# ── Listing + detail ───────────────────────────────────────────

@patterns_bp.route("/api/patterns")
def api_patterns():
    tag_filter = request.args.get("tag")
    cat_filter = request.args.get("category")

    with SessionLocal() as session:
        # source_kind='pattern' excludes wiki rows — those are surfaced
        # in the topics workspace, not the patterns page.
        stmt = (
            select(PatternDoc)
            .where(PatternDoc.source_kind == "pattern")
            .order_by(PatternDoc.category, PatternDoc.title)
        )
        if tag_filter:
            # doc_id ∈ docs tagged with `tag_filter`
            tagged_ids = select(DocTag.doc_id).join(
                Tag, Tag.id == DocTag.tag_id
            ).where(Tag.name == tag_filter)
            stmt = stmt.where(PatternDoc.id.in_(tagged_ids))
        if cat_filter:
            stmt = stmt.where(PatternDoc.category == cat_filter)
        docs = session.exec(stmt).all()

        # Tag names per doc, fetched in one query to avoid N+1.
        doc_ids = [d.id for d in docs if d.id is not None]
        tags_by_doc: dict[int, list[str]] = {}
        if doc_ids:
            tag_stmt = (
                select(DocTag.doc_id, Tag.name)
                .join(Tag, Tag.id == DocTag.tag_id)
                .where(DocTag.doc_id.in_(doc_ids))
            )
            for doc_id, tag_name in session.exec(tag_stmt).all():
                tags_by_doc.setdefault(doc_id, []).append(tag_name)

        categories = session.exec(
            select(PatternDoc.category)
            .where(PatternDoc.source_kind == "pattern")
            .distinct()
            .order_by(PatternDoc.category)
        ).all()

        tag_pop_stmt = (
            select(Tag.name, Tag.category,
                   func.count(DocTag.doc_id).label("doc_count"))
            .join(DocTag, DocTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .having(func.count(DocTag.doc_id) > 0)
            .order_by(func.count(DocTag.doc_id).desc())
        )
        tags_rows = [dict(r._mapping) for r in session.exec(tag_pop_stmt).all()]

    # Build the skills registry once and derive a procedure_id → skill_id
    # reverse index, so the per-row sync-state lookup is a dict get instead
    # of a fresh _build_skills() + linear scan per pattern.
    skills_snapshot = skill_registry.snapshot()
    proc_to_skill = skill_registry.procedure_to_skill_id_map(skills_snapshot)

    result_docs = []
    skill_states_map: dict[str, str] = {}
    for d in docs:
        entry = _pattern_to_dict(d)
        entry["tag_names"] = ", ".join(tags_by_doc.get(d.id, []))
        # Description is intentionally omitted from the list payload to keep
        # this endpoint cheap — clients that need it call GET /api/patterns/<slug>.
        result_docs.append(entry)
        sid = proc_to_skill.get(d.slug)
        if sid:
            skill_states_map[d.slug] = skill_sync.state(
                sid, entry=skills_snapshot[sid],
            )

    return jsonify({
        "docs": result_docs,
        "categories": categories,
        "tags": tags_rows,
        "tag_filter": tag_filter,
        "cat_filter": cat_filter,
        "skill_states": skill_states_map,
    })


@patterns_bp.route("/api/patterns/route")
def api_patterns_route():
    """EXPERIMENTAL. Dense (SkillRouter) routing for the pattern catalog."""
    from lib.patterns import pattern_router
    from lib.skills import skill_router
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "missing q"}), 400
    try:
        top_k = int(request.args.get("top_k", 10))
    except ValueError:
        top_k = 10
    rerank_flag = request.args.get("rerank", "true").lower() not in {"false", "0", "no"}

    try:
        hits = pattern_router.route(query, top_k=top_k, rerank=rerank_flag)
    except skill_router.DependencyError as exc:
        return jsonify({
            "error": "router dependencies missing",
            "detail": str(exc),
            "install_hint": "pip install -r requirements-router.txt",
        }), 503

    if not hits:
        return jsonify({
            "query": query, "docs": [], "score_kind": None,
            "hint": "no patterns indexed — run `regin pattern embed`",
        })

    slug_to_score = {h["slug"]: (h["score"], h["score_kind"]) for h in hits}
    with SessionLocal() as session:
        stmt = select(PatternDoc).where(PatternDoc.slug.in_(list(slug_to_score.keys())))
        docs = session.exec(stmt).all()
        doc_ids = [d.id for d in docs if d.id is not None]
        tags_by_doc: dict[int, list[str]] = {}
        if doc_ids:
            tag_stmt = (
                select(DocTag.doc_id, Tag.name)
                .join(Tag, Tag.id == DocTag.tag_id)
                .where(DocTag.doc_id.in_(doc_ids))
            )
            for doc_id, tag_name in session.exec(tag_stmt).all():
                tags_by_doc.setdefault(doc_id, []).append(tag_name)

    results = []
    for d in docs:
        score, kind = slug_to_score.get(d.slug, (0.0, ""))
        entry = _pattern_to_dict(d)
        entry["tag_names"] = ", ".join(tags_by_doc.get(d.id, []))
        entry["score"] = score
        results.append(entry)
    # Preserve route-order rather than alphabetical.
    order = {h["slug"]: i for i, h in enumerate(hits)}
    results.sort(key=lambda r: order.get(r["slug"], 999))
    return jsonify({
        "query": query,
        "docs": results,
        "score_kind": hits[0]["score_kind"] if hits else None,
    })


@patterns_bp.route("/api/patterns/embedding-coverage")
def api_patterns_embedding_coverage():
    """Surface dense-search index coverage to the Patterns UI.

    Lets the dense-search toolbar show a "12/15 embedded · 3 stale" chip
    so users can tell whether bad results are an algorithm failure or
    just a stale index.
    """
    from lib.patterns import pattern_router
    return jsonify(pattern_router.embedding_coverage())


@patterns_bp.route("/api/patterns/reindex", methods=["POST"])
@require_editor
def api_patterns_reindex():
    """Kick off `pattern embed` in a background thread, return 202.

    Re-embedding is idempotent (skips unchanged rows) so it's safe to
    call repeatedly from the UI. Wires through `index_patterns_best_effort`
    so missing torch/transformers degrades to a 503 with an install hint.
    """
    import threading
    from lib.patterns import pattern_router
    from lib.skills import skill_router
    try:
        skill_router.ensure_deps()
    except skill_router.DependencyError as exc:
        return jsonify({
            "error": "router dependencies missing",
            "detail": str(exc),
            "install_hint": "pip install -r requirements-router.txt",
        }), 503

    def _run():
        try:
            pattern_router.index_patterns()
        except Exception:
            pass

    threading.Thread(target=_run, name="pattern-reindex", daemon=True).start()
    return jsonify({"ok": True, "msg": "reindex started"}), 202


@patterns_bp.route("/api/patterns/<path:slug>")
def api_pattern_detail(slug):
    file_path = os.path.join(str(settings.patterns_dir), slug, "SKILL.md")
    if not os.path.exists(file_path):
        legacy = os.path.join(str(settings.patterns_dir), slug + ".md")
        if os.path.exists(legacy):
            file_path = legacy
        else:
            return jsonify({"error": "not found"}), 404

    with open(file_path, "r") as f:
        content = f.read()

    parts = content.split("---", 2)
    body = parts[2] if len(parts) >= 3 else content

    procedure_id = None
    if len(parts) >= 3:
        try:
            fm = yaml.safe_load(parts[1])
            procedure_id = fm.get("procedure") if fm else None
        except yaml.YAMLError:
            pass

    skill_id = skill_registry.skill_id_for_procedure(procedure_id) if procedure_id else None
    skill_state = skill_sync.state(skill_id) if skill_id else None
    enforcing_rules = rules_for_guide(procedure_id) if procedure_id else []
    attached_rule_bundles = _attached_rule_bundles_for_pattern(procedure_id)
    pattern_experiments = experiments.list_for_pattern(procedure_id) if procedure_id else []
    available_sections = experiments.list_sections(procedure_id) if procedure_id else []

    concealed_headings: set[str] = set()
    for exp in pattern_experiments:
        if exp.get("active"):
            concealed_headings.update(exp.get("sections", []))
    concealed_texts = []
    if concealed_headings:
        for heading in concealed_headings:
            text = heading.lstrip("#").strip()
            plain = re.sub(r"`([^`]+)`", r"\1", text)
            concealed_texts.append(plain)

    with SessionLocal() as session:
        doc = session.exec(
            select(PatternDoc)
            .where(PatternDoc.slug == slug)
            .where(PatternDoc.source_kind == "pattern")
        ).first()

        tags_rows: list[dict] = []
        all_tags: list[dict] = []

        if doc is not None:
            tag_stmt = (
                select(Tag.name, Tag.category)
                .join(DocTag, DocTag.tag_id == Tag.id)
                .where(DocTag.doc_id == doc.id)
            )
            tags_rows = [dict(r._mapping) for r in session.exec(tag_stmt).all()]

            all_tags = [
                {"name": n, "category": c}
                for n, c in session.exec(
                    select(Tag.name, Tag.category).order_by(Tag.category, Tag.name)
                ).all()
            ]

    return jsonify({
        "doc": _pattern_to_dict(doc) if doc else None,
        "tags": tags_rows,
        "all_tags": all_tags,
        "body_md": body,
        "description": doc.description or "",
        "skill_id": skill_id,
        "skill_state": skill_state,
        "procedure_id": procedure_id,
        "enforcing_rules": enforcing_rules,
        "attached_rule_bundles": attached_rule_bundles,
        "experiments": pattern_experiments,
        "available_sections": available_sections,
        "concealed_texts": concealed_texts,
    })


