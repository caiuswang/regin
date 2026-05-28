"""Plan file browsing and plan-mode session lifecycle endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import func, literal_column, text
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PlanSession, Repo, SessionRepo, SessionSpan, SkillRead
from lib.utils.pagination import clamp_size, keyset_page_stmt
from lib.trace.plans import get_plan, list_plans


plans_bp = Blueprint("plans", __name__)


def _attach_plan_repos(session, items) -> None:
    """Attach `repos`, `is_multi_repo`, `primary_repo` to plan row dicts.

    Plan rows link to their session by `session_id` (== `Session.trace_id`),
    so repo membership is read from `session_repos`. One batched query for
    the page; rows whose session has no repo tag get an empty list.
    """
    if not items:
        return
    session_ids = [it.get("session_id") for it in items if it.get("session_id")]
    by_sid: dict = {}
    if session_ids:
        rows = session.exec(
            select(SessionRepo.trace_id, SessionRepo.is_primary, Repo.name)
            .join(Repo, Repo.id == SessionRepo.repo_id)
            .where(SessionRepo.trace_id.in_(session_ids))
        ).all()
        for sid, is_primary, name in rows:
            by_sid.setdefault(sid, []).append(
                {"name": name, "is_primary": bool(is_primary)})
    for it in items:
        repos = by_sid.get(it.get("session_id"), [])
        repos.sort(key=lambda r: (not r["is_primary"], r["name"]))
        it["repos"] = repos
        it["is_multi_repo"] = len(repos) > 1
        it["primary_repo"] = next(
            (r["name"] for r in repos if r["is_primary"]), None)


# ── Plan file browsing ─────────────────────────────────────────

def _attach_plan_file_repos(plans) -> None:
    """Attach `repos` (sorted repo names) to each plan-file dict in place.

    A plan file is linked to a repo when one of its plan-sessions ran in a
    session tagged with that repo. One batched query over the visible
    filenames.
    """
    if not plans:
        return
    filenames = [p["filename"] for p in plans]
    by_file: dict = {}
    with SessionLocal() as session:
        rows = session.exec(
            select(PlanSession.plan_filename, Repo.name)
            .join(SessionRepo, SessionRepo.trace_id == PlanSession.session_id)
            .join(Repo, Repo.id == SessionRepo.repo_id)
            .where(PlanSession.plan_filename.in_(filenames))
            .distinct()
        ).all()
    for fname, name in rows:
        by_file.setdefault(fname, set()).add(name)
    for p in plans:
        p["repos"] = sorted(by_file.get(p["filename"], ()))


@plans_bp.route("/api/plans")
def api_plans():
    repo_filter = (request.args.get("repo") or "").strip()
    plans = list_plans()
    _attach_plan_file_repos(plans)
    if repo_filter:
        plans = [p for p in plans if repo_filter in p["repos"]]
    return jsonify({"plans": plans})


@plans_bp.route("/api/plans/<filename>")
def api_plan_detail(filename):
    plan = get_plan(filename)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404
    return jsonify(plan)


@plans_bp.route("/api/plans/<filename>/mentions")
def api_plan_mentions(filename):
    """Skills the plan's authoring session(s) actually read or invoked.

    Replaces the previous substring-match heuristic that flagged any skill
    id appearing as a literal substring in the plan text — that produced
    false positives (a `pattern_router` skill collides with a Python
    module reference) and false negatives (a skill used by the session
    but not named in the plan body). Now a session-based join: the
    authoring sessions come from `plan_sessions` (populated when the
    session writes/edits a plan), and the skills come from
    `skill_reads` rows scoped to those sessions.

    Returns `[]` when no PlanSession row attributes this plan to any
    session — which is the honest answer, not a guess from text search.
    """
    plan = get_plan(filename)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    with SessionLocal() as session:
        session_ids = session.exec(
            select(PlanSession.session_id)
            .where(PlanSession.plan_filename == filename)
            .distinct()
        ).all()
        skills: list[dict] = []
        if session_ids:
            rows = session.exec(
                select(SkillRead.skill_id,
                       func.max(SkillRead.read_at).label("last_read_at"))
                .where(SkillRead.session_id.in_(session_ids),
                       SkillRead.found == 1)
                .group_by(SkillRead.skill_id)
                .order_by(func.max(SkillRead.read_at).desc())
            ).all()
            skills = [
                {"skill_id": sid, "last_read_at": last_read_at}
                for sid, last_read_at in rows
            ]
    return jsonify({"skills": skills})


# ── Plan-mode session lifecycle ────────────────────────────────

@plans_bp.route("/api/plan-sessions", methods=["POST"])
def api_ingest_plan_session():
    """Record a plan-mode lifecycle event.

    `enter` deduplicates on (session_id, plan_filename) so repeated
    writes/edits to the same plan within a session collapse to one
    PlanSession row (the first write wins on started_at).
    `draft_completed` / `exit` rely on the `ended_at IS NULL` guard
    to target only the active row.
    """
    data = request.get_json(silent=True) or {}
    event = data.get("event")
    session_id = data.get("session_id")
    skipped_duplicate = False

    with SessionLocal() as session:
        if event == "enter":
            plan_filename = data.get("plan_filename")
            started_at = data.get("started_at")
            if session_id and plan_filename:
                existing = session.exec(
                    select(PlanSession).where(
                        PlanSession.session_id == session_id,
                        PlanSession.plan_filename == plan_filename,
                    ).limit(1)
                ).first()
                if existing is not None:
                    skipped_duplicate = True
            if not skipped_duplicate and session_id and plan_filename and started_at:
                session.add(PlanSession(
                    session_id=session_id, plan_filename=plan_filename,
                    started_at=started_at,
                ))
        elif event == "draft_completed" and session_id:
            rows = session.exec(
                select(PlanSession).where(
                    PlanSession.session_id == session_id,
                    PlanSession.ended_at.is_(None),
                )
            ).all()
            for row in rows:
                row.draft_completed_at = data.get("draft_completed_at")
                row.review_started_at = data.get("review_started_at")
                session.add(row)
        elif event == "exit" and session_id:
            rows = session.exec(
                select(PlanSession).where(
                    PlanSession.session_id == session_id,
                    PlanSession.ended_at.is_(None),
                )
            ).all()
            for row in rows:
                row.ended_at = data.get("ended_at")
                session.add(row)
        session.commit()

    return jsonify({"ok": True, "skipped_duplicate": skipped_duplicate})


@plans_bp.route("/api/plan-sessions")
def api_plan_sessions():
    """Keyset-paginated plan-session log.

    Rows are continuously ingested while a user browses, so cursor
    beats offset. Cursor is (started_at DESC, id DESC). Now on the
    SQLModel-native helper `keyset_page_stmt`.
    """
    plan_filter = request.args.get("plan")
    session_filter = request.args.get("session")
    repo_filter = (request.args.get("repo") or "").strip()
    only_active = request.args.get("active") == "1"
    include_tests = request.args.get("include_tests", "false").lower() in ("1", "true", "yes")
    cursor_token = request.args.get("cursor")
    size = clamp_size(request.args.get("size"), default=100)

    def _to_dict(row: PlanSession) -> dict:
        return {
            "id": row.id, "session_id": row.session_id,
            "plan_filename": row.plan_filename,
            "started_at": row.started_at, "ended_at": row.ended_at,
            "draft_completed_at": row.draft_completed_at,
            "review_started_at": row.review_started_at,
        }

    with SessionLocal() as session:
        stmt = select(PlanSession)
        if plan_filter:
            stmt = stmt.where(PlanSession.plan_filename == plan_filter)
        if session_filter:
            stmt = stmt.where(PlanSession.session_id == session_filter)
        if only_active:
            stmt = stmt.where(PlanSession.ended_at.is_(None))
        if repo_filter:
            # Plans whose session touched this repo. Plans whose session
            # has no repo tag drop out only while a repo is selected.
            stmt = stmt.where(PlanSession.session_id.in_(
                select(SessionRepo.trace_id)
                .join(Repo, Repo.id == SessionRepo.repo_id)
                .where(Repo.name == repo_filter)
            ))
        if not include_tests:
            # Exclude sessions that are marked as test via any span's is_test attribute.
            test_trace_ids = (
                select(SessionSpan.trace_id)
                .where(literal_column("json_extract(attributes, '$.is_test')") == 1)
                .distinct()
            )
            stmt = stmt.where(PlanSession.session_id.not_in(test_trace_ids))

        page = keyset_page_stmt(
            session, stmt,
            order_cols=[(PlanSession.started_at, "DESC"), (PlanSession.id, "DESC")],
            cursor_token=cursor_token, size=size,
            row_to_dict=_to_dict,
        )
        _attach_plan_repos(session, page.items)

    envelope = page.to_envelope()
    envelope["plan_filter"] = plan_filter
    envelope["session_filter"] = session_filter
    envelope["repo_filter"] = repo_filter
    envelope["only_active"] = only_active
    envelope["include_tests"] = include_tests
    return jsonify(envelope)
