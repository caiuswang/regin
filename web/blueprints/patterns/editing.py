"""Pattern endpoints split by purpose."""

from __future__ import annotations

import hashlib
import json
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
from lib.orm.models import DocTag, PatternDeployment, PatternDoc, Repo, Tag

from lib import audit, experiments
from lib.activity_log import get_activity_logger
from lib.patterns import pattern_deployments
from lib.skills import skill_registry, skill_sync

from web.blueprints.patterns import patterns_bp
from web.blueprints.patterns._helpers import (
    _get_pattern_or_404, _pattern_to_dict, _now_iso,
    _attached_rule_bundles_for_pattern,
    _set_frontmatter_description,
    _sync_doc_from_frontmatter,
    _yaml_double_quote_escape,
)


# ── Create / edit / delete ─────────────────────────────────────

def _parse_create_payload(data):
    """Pull and normalize the create-pattern fields off a request body."""
    return (
        (data.get("title") or "").strip(),
        (data.get("description") or "").strip(),
        (data.get("slug") or "").strip(),
        data.get("tags", []),
    )


def _attach_tags(session, doc, tags):
    """Link a freshly-flushed ``doc`` to existing tags by name (INSERT OR IGNORE)."""
    for tag_name in tags:
        tag_row = session.exec(
            select(Tag).where(Tag.name == tag_name)
        ).first()
        if tag_row is not None:
            # INSERT OR IGNORE — check before insert.
            link_exists = session.exec(
                select(DocTag).where(
                    DocTag.doc_id == doc.id,
                    DocTag.tag_id == tag_row.id,
                )
            ).first()
            if link_exists is None:
                session.add(DocTag(doc_id=doc.id, tag_id=tag_row.id))


def _audit_create_actor(user):
    """Resolve the (id, username) tuple for the create-pattern audit record."""
    return (
        user["id"] if user else None,
        user["username"] if user else "anonymous",
    )


@patterns_bp.route("/api/patterns/create", methods=["POST"])
@require_editor
def api_create_pattern():
    """Create a new manual pattern with a SKILL.md template."""
    data = request.get_json(silent=True) or {}
    title, description, slug, tags = _parse_create_payload(data)

    if not title:
        return jsonify({"ok": False, "msg": "Title is required"}), 400
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        return jsonify({"ok": False, "msg": "Invalid slug (use lowercase, hyphens, numbers)"}), 400

    pattern_dir = os.path.join(str(settings.patterns_dir), slug)
    if os.path.exists(pattern_dir):
        return jsonify({"ok": False, "msg": f'Pattern "{slug}" already exists'}), 409

    with SessionLocal() as session:
        existing = session.exec(
            select(PatternDoc.id).where(PatternDoc.slug == slug)
        ).first()
        if existing is not None:
            return jsonify({"ok": False, "msg": f'Pattern "{slug}" already in database'}), 409

        os.makedirs(pattern_dir, exist_ok=True)
        skill_path = os.path.join(pattern_dir, "SKILL.md")
        title_escaped = _yaml_double_quote_escape(title)
        collapsed_desc = re.sub(r"\s+", " ", description).strip() if description else ""
        with open(skill_path, "w") as f:
            f.write("---\n")
            f.write(f'title: "{title_escaped}"\n')
            if collapsed_desc:
                desc_escaped = _yaml_double_quote_escape(collapsed_desc)
                f.write(f'description: "{desc_escaped}"\n')
            f.write(f"procedure: {slug}\n")
            f.write("manual: true\n")
            f.write("---\n\n")
            f.write(f"# {title}\n\n")
            f.write("## Disciplines\n\n")
            f.write("<!-- Add rules and best practices here -->\n\n")
            f.write("## Anti-Patterns\n\n")
            f.write("<!-- Add common mistakes to avoid here -->\n")

        with open(skill_path, "rb") as f:
            c_hash = hashlib.sha256(f.read()).hexdigest()
        rel_path = os.path.relpath(skill_path, str(settings.project_root))

        doc = PatternDoc(
            slug=slug, title=title, file_path=rel_path,
            category="procedure",
            content_hash=c_hash,
            description=collapsed_desc or None,
        )
        session.add(doc)
        session.flush()  # populate doc.id

        _attach_tags(session, doc, tags)
        session.commit()

    actor_id, actor_name = _audit_create_actor(get_current_user())
    audit.log_action(
        actor_id, actor_name,
        "create_pattern", f"patterns/{slug}",
        {"title": title, "tags": tags},
    )

    return jsonify({"ok": True, "msg": f'Created pattern "{slug}"', "slug": slug})


@patterns_bp.route("/api/patterns/<path:slug>/content", methods=["POST"])
@require_editor
def api_save_pattern_content(slug):
    """Save the markdown body of a pattern's SKILL.md, and re-sync the
    DB-side `title` / `description` from the on-disk frontmatter so manual
    edits propagate to the WebUI without requiring a re-import."""
    data = request.get_json(silent=True) or {}
    body = data.get("body", "")
    if body is None:
        return jsonify({"ok": False, "msg": "Body is required"}), 400

    with SessionLocal() as session:
        doc = _get_pattern_or_404(session, slug)

        file_path = os.path.join(str(settings.project_root), doc.file_path)
        if not os.path.isfile(file_path):
            return jsonify({"error": "SKILL.md file not found on disk"}), 404

        with open(file_path, "r") as f:
            raw = f.read()
        parts = raw.split("---", 2)
        frontmatter = parts[1] if len(parts) >= 3 else ""

        with open(file_path, "w") as f:
            if frontmatter:
                f.write(f"---{frontmatter}---\n")
            f.write(body)

        with open(file_path, "rb") as f:
            c_hash = hashlib.sha256(f.read()).hexdigest()
        doc.content_hash = c_hash
        doc.updated_at = _now_iso()
        _sync_doc_from_frontmatter(doc, frontmatter)
        session.add(doc)
        session.commit()

    user = get_current_user()
    audit.log_action(
        user["id"] if user else None,
        user["username"] if user else "anonymous",
        "edit_pattern", f"patterns/{slug}",
    )
    return jsonify({"ok": True, "msg": "Content saved"})


@patterns_bp.route("/api/patterns/<path:slug>/description", methods=["POST"])
@require_editor
def api_save_pattern_description(slug):
    """Update the YAML frontmatter ``description`` field for a pattern.

    Empty/whitespace input removes the description; otherwise it is
    collapsed to a single line and written as a double-quoted scalar.
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("description")
    if raw is None:
        return jsonify({"ok": False, "msg": "description is required"}), 400

    with SessionLocal() as session:
        doc = _get_pattern_or_404(session, slug)

        file_path = os.path.join(str(settings.project_root), doc.file_path)
        if not os.path.isfile(file_path):
            return jsonify({"error": "SKILL.md file not found on disk"}), 404

        with open(file_path, "r") as f:
            content = f.read()

        updated = _set_frontmatter_description(content, str(raw))
        if updated != content:
            with open(file_path, "w") as f:
                f.write(updated)
            with open(file_path, "rb") as f:
                c_hash = hashlib.sha256(f.read()).hexdigest()
            doc.content_hash = c_hash
            doc.updated_at = _now_iso()
            collapsed = re.sub(r"\s+", " ", str(raw)).strip()
            doc.description = collapsed or None
            session.add(doc)
            session.commit()

    user = get_current_user()
    audit.log_action(
        user["id"] if user else None,
        user["username"] if user else "anonymous",
        "edit_pattern_description", f"patterns/{slug}",
    )
    return jsonify({"ok": True, "msg": "Description saved"})


@patterns_bp.route("/api/patterns/<path:slug>/tags", methods=["POST"])
@require_editor
def api_update_pattern_tags(slug):
    data = request.get_json(silent=True) or {}
    tag_list = data.get("tags", [])
    new_tag = (data.get("new_tag") or "").strip()

    with SessionLocal() as session:
        doc = _get_pattern_or_404(session, slug)

        # Wipe existing links, rebuild from tag_list, optionally add new_tag.
        for link in session.exec(
            select(DocTag).where(DocTag.doc_id == doc.id)
        ).all():
            session.delete(link)

        for tag_name in tag_list:
            tag = session.exec(
                select(Tag).where(Tag.name == tag_name)
            ).first()
            if tag is not None:
                session.add(DocTag(doc_id=doc.id, tag_id=tag.id))

        if new_tag:
            existing = session.exec(
                select(Tag).where(Tag.name == new_tag)
            ).first()
            if existing is None:
                existing = Tag(name=new_tag, category="concept")
                session.add(existing)
                session.flush()
            # INSERT OR IGNORE on link.
            link_exists = session.exec(
                select(DocTag).where(
                    DocTag.doc_id == doc.id,
                    DocTag.tag_id == existing.id,
                )
            ).first()
            if link_exists is None:
                session.add(DocTag(doc_id=doc.id, tag_id=existing.id))

        session.commit()
    return jsonify({"ok": True, "msg": "Tags updated"})


def _cleanup_pattern_grit_rules(slug: str) -> None:
    """Drop any grit rules this pattern installed on import (guide == slug)
    from the active grit_dir + regenerate the index, then redeploy the
    grit-rules skill. Symmetric with the import-time grit-merge. Best-effort:
    the pattern's DB row is already gone, so a grit-side failure must not
    500 the delete."""
    try:
        from lib.skills.skill_deployer import deploy_rules_index_skill
        if grit_rule_index.remove_guide_rules(slug).get("removed"):
            deploy_rules_index_skill(RULES_MD_PATH)
    except Exception:
        get_activity_logger("patterns").error(
            "grit_rules_cleanup_failed", slug=slug, exc_info=True,
        )


@patterns_bp.route("/api/patterns/<path:slug>/delete", methods=["POST"])
@require_editor
def api_delete_pattern(slug):
    """Delete a pattern end-to-end — every deployed skill directory,
    deployment rows, source directory, and pattern_docs row."""
    removed_paths: list[str] = []
    with SessionLocal() as session:
        doc = _get_pattern_or_404(session, slug)

        skill_id = skill_registry.skill_id_for_procedure(slug)

        deployments = pattern_deployments.list_deployments(pattern_slug=slug)
        for d in deployments:
            path = d.get("deployed_path")
            if path and os.path.isdir(path):
                shutil.rmtree(path)
                removed_paths.append(path)

        # Drop all deployment rows for this slug.
        for dep in session.exec(
            select(PatternDeployment).where(PatternDeployment.pattern_slug == slug)
        ).all():
            session.delete(dep)

        if skill_id:
            try:
                skill_sync.undeploy(skill_id)
            except Exception:
                pass

        pattern_dir = os.path.join(str(settings.patterns_dir), slug)
        if os.path.isdir(pattern_dir):
            shutil.rmtree(pattern_dir)

        # Drop doc_tags links explicitly before the pattern_docs row
        # (doc_tags has ON DELETE CASCADE in schema.sql, but ORM deletes
        # don't cascade without cascade= config — delete explicitly).
        for link in session.exec(
            select(DocTag).where(DocTag.doc_id == doc.id)
        ).all():
            session.delete(link)

        session.delete(doc)
        session.commit()

    _cleanup_pattern_grit_rules(slug)

    user = get_current_user()
    audit.log_action(
        user["id"] if user else None,
        user["username"] if user else "anonymous",
        "delete_pattern", f"patterns/{slug}",
    )

    return jsonify({
        "ok": True,
        "msg": f'Deleted pattern "{slug}"'
               + (f" + {len(removed_paths)} deployed skill(s)" if removed_paths else ""),
        "removed_deployments": removed_paths,
    })


def _import_folder_upload(uploads, paths_raw, *, force, target_slug):
    """Assemble a browser folder upload (multiple files + a parallel JSON
    array of their relative paths) into a pattern via import_files."""
    from lib.patterns import pattern_importer

    try:
        paths = json.loads(paths_raw)
    except (ValueError, TypeError):
        raise pattern_importer.ImportError_(
            "invalid 'paths' field (expected a JSON array)")
    if not isinstance(paths, list) or len(paths) != len(uploads):
        raise pattern_importer.ImportError_(
            "'paths' length does not match the uploaded files")
    files = [(str(p), fs.read()) for p, fs in zip(paths, uploads)]
    return pattern_importer.import_files(
        files, force=force, target_slug=target_slug)


def _grit_import_note(result) -> str:
    """One-line suffix describing grit rules merged on import (empty if none)."""
    if not result.grit_rules:
        return ""
    langs = f" ({', '.join(result.grit_languages)})" if result.grit_languages else ""
    return (f" Merged {len(result.grit_rules)} grit rule(s){langs}; "
            "activate by pushing the pattern.")


@patterns_bp.route("/api/patterns/import", methods=["POST"])
@require_editor
def api_import_pattern():
    """Import a skill as a new pattern.

    Accepts either a single file (.zip bundle or bare SKILL.md) or a
    multi-file folder upload (all files of one skill folder).

    Query params:
        force (bool): Overwrite an existing pattern with the same slug.
    Form fields:
        file (required): One upload, or repeated for a folder upload.
        paths (optional): JSON array of each file's relative path, in the
            same order as the `file` parts. Present for folder uploads.
        slug (optional): Use this slug instead of the derived one.
    """
    from lib.patterns import pattern_importer

    uploads = request.files.getlist("file")
    if not uploads or not uploads[0].filename:
        return jsonify({"ok": False, "msg": 'missing file field "file"'}), 400

    force = request.args.get("force", "false").lower() in ("1", "true", "yes")
    target_slug = (request.form.get("slug") or "").strip() or None
    paths_raw = request.form.get("paths")

    try:
        if paths_raw:
            result = _import_folder_upload(
                uploads, paths_raw, force=force, target_slug=target_slug,
            )
        else:
            upload = uploads[0]
            result = pattern_importer.import_upload(
                upload.filename, upload.read(),
                force=force, target_slug=target_slug,
            )
    except pattern_importer.ImportConflictError as e:
        return jsonify({"ok": False, "msg": str(e), "conflict": True}), 409
    except pattern_importer.ImportError_ as e:
        return jsonify({"ok": False, "msg": str(e)}), 400

    user = get_current_user()
    audit.log_action(
        user["id"] if user else None,
        user["username"] if user else "anonymous",
        "import_pattern", f"patterns/{result.slug}",
        {"shape": result.shape, "file_count": result.file_count, "force": force},
    )
    return jsonify({
        "ok": True,
        "slug": result.slug,
        "title": result.title,
        "pattern_dir": result.pattern_dir,
        "shape": result.shape,
        "file_count": result.file_count,
        "grit_rules": result.grit_rules,
        "grit_languages": result.grit_languages,
        "enabled_languages": result.enabled_languages,
        "msg": f'Imported "{result.title}" as pattern {result.slug} '
               f"({result.file_count} file(s)) — review + Push to deploy."
               + _grit_import_note(result),
    })


@patterns_bp.route("/api/patterns/import-dir/scan", methods=["POST"])
@require_editor
def api_import_dir_scan():
    """Scan a directory for `<root>/<name>/SKILL.md` candidates.

    Body: {"path": "<server-side dir>"}.
    Returns: {"ok": true, "path": "<abs>", "candidates": [...]}
    """
    import os
    from lib.patterns import pattern_importer

    body = request.get_json(silent=True) or {}
    raw = (body.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "msg": "missing 'path'"}), 400
    expanded = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(expanded):
        return jsonify({"ok": False, "msg": f"not a directory: {expanded}"}), 400

    try:
        scanned = pattern_importer.scan_skill_directory(expanded)
    except pattern_importer.ImportError_ as e:
        return jsonify({"ok": False, "msg": str(e)}), 400

    return jsonify({
        "ok": True,
        "path": expanded,
        "candidates": [
            {
                "name": s.name,
                "derived_slug": s.derived_slug,
                "conflict": s.conflict,
                "error": s.error,
            }
            for s in scanned
        ],
    })


@patterns_bp.route("/api/patterns/import-dir", methods=["POST"])
@require_editor
def api_import_dir():
    """Batch-import every `<root>/<name>/SKILL.md` under `path`.

    Body: {
      "path": "<server-side dir>",
      "on_conflict": "skip" | "overwrite" | "rename",  # default: skip
      "dry_run": bool                                   # default: false
    }
    """
    import os
    from lib.patterns import pattern_importer

    body = request.get_json(silent=True) or {}
    raw = (body.get("path") or "").strip()
    if not raw:
        return jsonify({"ok": False, "msg": "missing 'path'"}), 400
    on_conflict = (body.get("on_conflict") or "skip").strip().lower()
    dry_run = bool(body.get("dry_run", False))

    expanded = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(expanded):
        return jsonify({"ok": False, "msg": f"not a directory: {expanded}"}), 400

    try:
        results = pattern_importer.batch_import_skill_directory(
            expanded, on_conflict=on_conflict, dry_run=dry_run,
        )
    except pattern_importer.ImportError_ as e:
        return jsonify({"ok": False, "msg": str(e)}), 400

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    if not dry_run and (counts.get("imported") or counts.get("overwritten")
                         or counts.get("renamed")):
        user = get_current_user()
        audit.log_action(
            user["id"] if user else None,
            user["username"] if user else "anonymous",
            "import_dir", f"path:{expanded}",
            {"on_conflict": on_conflict, "counts": counts},
        )

    return jsonify({
        "ok": True,
        "path": expanded,
        "on_conflict": on_conflict,
        "dry_run": dry_run,
        "counts": counts,
        "results": [
            {
                "name": r.name,
                "status": r.status,
                "slug": r.slug,
                "title": r.title,
                "file_count": r.file_count,
                "error": r.error,
                "grit_rules": r.grit_rules,
                "grit_languages": r.grit_languages,
                "enabled_languages": r.enabled_languages,
            }
            for r in results
        ],
    })


@patterns_bp.route("/api/skillhub-status")
def api_skillhub_status():
    """Report whether the optional regin-skillhub sibling is installed."""
    from lib.patterns import pattern_promoter
    return jsonify(pattern_promoter.is_available())


@patterns_bp.route("/api/patterns/<path:slug>/promote", methods=["POST"])
@require_editor
def api_promote_pattern(slug):
    """Promote a pattern into a regin-skillhub skill bundle."""
    from lib.patterns import pattern_promoter

    body = request.get_json(silent=True) or {}
    version = body.get("version") or "1.0.0"
    skillhub_url = body.get("skillhub_url")
    force = bool(body.get("force"))

    pattern_dir = os.path.join(str(settings.patterns_dir), slug)
    if not os.path.isdir(pattern_dir):
        return jsonify({"error": f"pattern {slug!r} not found"}), 404

    try:
        result = pattern_promoter.promote(
            slug, version=version,
            skillhub_url=skillhub_url, force=force,
        )
    except pattern_promoter.PromoteError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    user = get_current_user()
    audit.log_action(
        user["id"] if user else None,
        user["username"] if user else "anonymous",
        "promote_pattern", f"patterns/{slug}@{version}",
    )
    return jsonify({
        "ok": True,
        "bundle_filename": result["bundle_filename"],
        "version": result["version"],
        "url": result["url"],
        "response": result["response"],
    })


