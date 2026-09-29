"""Shared helpers used by multiple pattern endpoint modules."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import yaml
from flask import abort, jsonify, make_response
from sqlmodel import select

from lib import rule_engines
from lib.settings import settings
from web.blueprints import patterns as _pkg
from lib.orm.models import PatternDoc, PatternDeployment, Repo, Tag, DocTag
from lib.rules import engine_rule_disable
from lib.rules.grit_rule_index import rules_for_guide


def _get_pattern_or_404(session, slug):
    """Fetch a PatternDoc by slug or abort with a JSON 404.

    Extracted because four mutation endpoints (content, tags, source,
    delete) duplicated the same three-line guard. Returns the doc so
    callers can continue straight through; the abort stops handler
    execution so there is no caller-side check.

    Wiki rows (source_kind='wiki') share the pattern_docs table but
    are managed in the topics workspace; they're invisible to the
    patterns CRUD so direct-URL pokes hit a 404 instead of a half-
    broken editor.
    """
    doc = session.exec(
        select(PatternDoc)
        .where(PatternDoc.slug == slug)
        .where(PatternDoc.source_kind == "pattern")
    ).first()
    if doc is None:
        abort(make_response(jsonify({"error": "Pattern not found"}), 404))
    return doc


def _pattern_to_dict(pd: PatternDoc) -> dict:
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "file_path": pd.file_path, "category": pd.category,
        "content_hash": pd.content_hash,
        "created_at": pd.created_at, "updated_at": pd.updated_at,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _frontmatter_description(content: str) -> str:
    """Return the description from SKILL.md frontmatter, collapsed to a
    single line. Empty string if absent or unparseable."""
    if not content.startswith("---"):
        return ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return ""
    if not isinstance(fm, dict):
        return ""
    desc = fm.get("description")
    if desc is None:
        return ""
    return re.sub(r"\s+", " ", str(desc)).strip()


def _set_frontmatter_description(content: str, new_description: str) -> str:
    """Return ``content`` with the frontmatter ``description`` field set.

    Edits surgically: replaces the existing ``description:`` block (single
    line plus any indented continuation lines) or inserts a new
    ``description:`` line after ``title:`` (or at the top of the
    frontmatter). Other fields are preserved byte-for-byte to avoid YAML
    churn. Empty/whitespace input removes the description entirely.
    """
    collapsed = re.sub(r"\s+", " ", new_description or "").strip()

    if not content.startswith("---"):
        if not collapsed:
            return content
        escaped = _yaml_double_quote_escape(collapsed)
        return f'---\ndescription: "{escaped}"\n---\n\n{content}'

    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    fm_inner = parts[1]
    rest = "---" + parts[2]

    if not collapsed:
        if _FM_DESCRIPTION_RE.search(fm_inner):
            new_fm = _FM_DESCRIPTION_RE.sub(lambda m: m.group(1) or "", fm_inner, count=1)
            return f"---{new_fm}{rest}"
        return content

    escaped = _yaml_double_quote_escape(collapsed)
    new_line = f'description: "{escaped}"\n'
    return f"---{_frontmatter_with_description_line(fm_inner, new_line)}{rest}"


_FM_DESCRIPTION_RE = re.compile(
    r"(^|\n)description:[^\n]*(?:\n[ \t]+[^\n]*)*\n",
)
_FM_TITLE_RE = re.compile(
    r"(^|\n)title:[^\n]*(?:\n[ \t]+[^\n]*)*\n",
)


def _frontmatter_with_description_line(fm_inner: str, new_line: str) -> str:
    """Replace the existing description block, else insert after `title:`,
    else at the top of the frontmatter."""
    if _FM_DESCRIPTION_RE.search(fm_inner):
        return _FM_DESCRIPTION_RE.sub(
            lambda m: (m.group(1) or "") + new_line, fm_inner, count=1)
    m = _FM_TITLE_RE.search(fm_inner)
    if m:
        return fm_inner[: m.end()] + new_line + fm_inner[m.end():]
    if fm_inner.startswith("\n"):
        return "\n" + new_line + fm_inner.lstrip("\n")
    return new_line + fm_inner


def _sync_doc_from_frontmatter(doc: PatternDoc, frontmatter: str) -> None:
    """Update `doc.title` and `doc.description` from a SKILL.md YAML
    frontmatter block (the text between the `---` fences, no fences).

    - Title is only updated if frontmatter carries a non-empty string —
      the DB column is NOT NULL.
    - Description is updated whenever the key is present; an empty value
      clears the column to NULL. A missing key leaves it alone (so a body
      save that drops the frontmatter line by accident doesn't nuke it).

    Parse failures are swallowed silently — the body save itself succeeded;
    we don't want a malformed YAML header to roll back a content edit.
    """
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return
    if not isinstance(meta, dict):
        return
    title = meta.get('title')
    if isinstance(title, str) and title.strip():
        doc.title = title.strip()
    if 'description' in meta:
        desc = meta.get('description')
        if isinstance(desc, str) and desc.strip():
            doc.description = desc.strip()
        else:
            doc.description = None


def _yaml_double_quote_escape(value: str) -> str:
    """Minimal escaping for a YAML double-quoted scalar on a single line."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _read_pattern_description(file_path: str) -> str:
    """Read SKILL.md at ``file_path`` (relative to PROJECT_ROOT) and return
    its frontmatter description. Empty string on any IO/parse error."""
    if not file_path:
        return ""
    abs_path = os.path.join(str(settings.project_root), file_path)
    if not os.path.isfile(abs_path):
        return ""
    try:
        with open(abs_path, "r") as f:
            return _frontmatter_description(f.read())
    except OSError:
        return ""


def _attached_rule_bundles_for_pattern(procedure_id: str | None) -> list[dict]:
    """Return rule bundles whose checker rules are attached to a pattern.

    A non-grit engine is "attached" to `procedure_id` when ANY of:
      - `engine.id == procedure_id` (bundle directory IS the pattern dir, e.g.
        `frontend-style-convention`)
      - the engine emits at least one rule whose `metadata.guide` equals
        `procedure_id` (this is the unification path for auto-skill engines
        like radon, whose engine.id is generic but whose rules carry a
        pattern-specific guide). Only those guide-matching rules are
        returned for such engines, so unrelated rules don't bleed in.
    """
    if not procedure_id:
        return []

    bundles: list[dict] = []
    for engine in rule_engines.all_engines():
        engine_kind = getattr(engine, "kind", "")
        if engine_kind == "grit":
            continue
        rules_iter = _attached_engine_rules(engine, procedure_id)
        if rules_iter is None:
            continue
        off = engine_rule_disable.disabled_ids(engine.id)
        bundles.append({
            "engine_id": engine.id,
            "engine_kind": engine_kind,
            "title": f"{engine.id} rule bundle",
            "description": _bundle_description(engine),
            "invocation_hint": (
                f"regin rules run --engine {engine.id} --rule <rule-id> "
                "--repo <repo-root> --file <relative-path>"
            ),
            "rules": [_bundle_rule_dict(rule, off) for rule in rules_iter],
        })
    return bundles


def _attached_engine_rules(engine, procedure_id: str) -> list | None:
    """The engine's rules attached to `procedure_id`, or None when the engine
    is not attached at all.

    If the engine itself maps to this pattern (bundle-IS-pattern), expose
    every rule it owns. Otherwise the attachment is "some rules carry
    guide=procedure_id" — emit only those, so unrelated rules from a shared
    engine don't bleed in.
    """
    parsed = list(engine.parse_rules())
    if engine.id == procedure_id:
        return parsed
    guide_matches = [r for r in parsed if r.metadata.get("guide") == procedure_id]
    return guide_matches or None


def _bundle_rule_dict(rule, off) -> dict:
    return {
        "id": rule.id,
        "summary": rule.summary,
        "severity": rule.severity,
        "engine": rule.engine,
        "source_file": rule.source_file,
        "checker": rule.metadata.get("checker"),
        "category": rule.metadata.get("category"),
        "check_kind": rule.metadata.get("check_kind"),
        "wcag_ref": rule.metadata.get("wcag_ref"),
        "triggers": list(rule.triggers),
        "disabled": rule.id in off,
    }


def _bundle_description(engine) -> str:
    manifest = getattr(engine, "manifest", None)
    raw_description = getattr(manifest, "description", None) or ""
    return (
        raw_description.strip()
        or "Portable rule bundle attached to this pattern skill."
    )

