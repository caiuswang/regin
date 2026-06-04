"""Import a skill bundle or bare SKILL.md as a new **pattern** under
`patterns/<slug>/`, leaving the deploy-to-~/.claude/skills step to the
normal regin Push flow.

Accepts:

- **Zip bundle** — regin-skillhub `.zip` layout: manifest.json +
  SKILL.md + optional content.md + references/ + other sibling files.
  Everything under the archive is copied into `patterns/<slug>/`
  verbatim.

- **Single SKILL.md** — just the markdown file. Frontmatter `name:` is
  mandatory (it becomes the pattern slug).

The SKILL.md frontmatter is rewritten from Claude Code format
(`name`, `description`) to regin format (`title`, `procedure`,
`source_repos`, `manual`, etc.) so the existing registry, sync, and
Push machinery treat the import like any other manual pattern.

Body text is appended as the pattern body. When the bundle already has
the shim + `content.md` split, `content.md` is treated as the real
body (shim discarded) and written back as the single SKILL.md body.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

from lib.settings import settings
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag
from lib.activity_log import get_activity_logger as _get_activity_logger


def _patterns_log():
    return _get_activity_logger("patterns")


_VALID_SLUG = re.compile(r'^[a-z][a-z0-9-]*$')


class ImportError_(Exception):
    """Raised when a pattern import cannot proceed."""


class ImportConflictError(ImportError_):
    """Raised when a pattern with the same slug already exists.

    Distinct from generic ImportError_ so callers can return 409
    instead of 400 and prompt the user for overwrite/rename.
    """


@dataclass
class ImportResult:
    slug: str
    title: str
    pattern_dir: str
    shape: str          # 'zip' | 'skill-md'
    file_count: int
    doc_id: int
    # Populated when the bundle ships grit rules that were merged into the
    # active grit_dir (see `_merge_grit_rules`). All empty otherwise.
    grit_rules: list[str] = field(default_factory=list)
    grit_languages: list[str] = field(default_factory=list)
    enabled_languages: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Tolerant of simple YAML only."""
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + len('\n---\n'):]
    fm: dict = {}
    current: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith(' ') and current:
            fm[current] = str(fm[current]) + ' ' + line.strip()
            continue
        if ':' in line:
            key, _, val = line.partition(':')
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            fm[key.strip()] = val
            current = key.strip()
    return fm, body


def _validate_slug(slug: str) -> None:
    if not slug:
        raise ImportError_(
            'missing `name:` in SKILL.md frontmatter — cannot derive pattern slug',
        )
    if not _VALID_SLUG.match(slug):
        raise ImportError_(
            f'invalid slug {slug!r} (must match [a-z][a-z0-9-]*)',
        )


def _derive_title(fm: dict, slug: str) -> str:
    """Use the skill's own `name` as the pattern title, verbatim.

    Deliberately does NOT synthesize a title from the description so the
    pattern title matches the skill name exactly in listings.
    """
    name = (fm.get('name') or fm.get('procedure') or '').strip()
    return name or slug


def _build_pattern_skill_md(slug: str, title: str, body: str,
                            manifest: dict | None = None) -> str:
    """Render pattern-format SKILL.md: regin frontmatter + body."""
    source_repos = ['imported']
    if manifest and manifest.get('origin'):
        origin = manifest['origin']
        repos = origin.get('source_repos') or []
        if origin.get('source_repo'):
            repos = [origin['source_repo']] + repos
        if repos:
            source_repos = list(dict.fromkeys(repos))
    repos_yaml = '[' + ', '.join(source_repos) + ']'
    now = _dt.datetime.now().isoformat(timespec='seconds')

    title_escaped = title.replace('"', '\\"')
    description = str((manifest or {}).get('description') or '').strip()
    description_escaped = description.replace('"', '\\"')

    lines = [
        '---',
        f'title: "{title_escaped}"',
    ]
    if description:
        lines.append(f'description: "{description_escaped}"')
    lines.extend([
        f'procedure: {slug}',
        f'source_repos: {repos_yaml}',
        'manual: true',
        f'imported_at: "{now}"',
        '---',
    ])
    fm = '\n'.join(lines) + '\n'
    body = body.lstrip('\n')
    if not body.endswith('\n'):
        body += '\n'
    return fm + '\n' + body


def _choose_body(skill_md_body: str, content_md: str | None) -> str:
    """Prefer content.md (real procedural text) when it exists and the
    SKILL.md body is a shim (small + references content.md)."""
    if content_md and 'content.md' in skill_md_body.lower() and len(skill_md_body) < 1200:
        return content_md
    return skill_md_body or content_md or ''


def _copy_extras(root: str, dest: str, exclude: set[str]) -> int:
    """Copy any file under `root` (non-recursive for top-level files AND
    recursive for subdirs) into `dest/`, except names in `exclude`.
    Returns number of files copied."""
    count = 0
    for name in sorted(os.listdir(root)):
        if name in exclude:
            continue
        src = os.path.join(root, name)
        dst = os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            count += sum(len(files) for _, _, files in os.walk(dst))
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
            count += 1
    return count


def _remove_pattern_doc(slug: str) -> None:
    """Remove a pattern and all its links from the database."""
    with SessionLocal() as session:
        doc = session.exec(
            select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        if doc is not None:
            session.delete(doc)
            session.commit()


def _register_pattern_doc(slug: str, title: str, skill_path: str) -> int:
    """Insert into pattern_docs. Returns the new doc id."""
    rel_path = os.path.relpath(skill_path, str(settings.project_root))
    with open(skill_path, 'rb') as f:
        c_hash = hashlib.sha256(f.read()).hexdigest()

    with SessionLocal() as session:
        existing = session.exec(
            select(PatternDoc.id).where(PatternDoc.slug == slug)
        ).first()
        if existing is not None:
            raise ImportConflictError(
                f'pattern {slug!r} already exists in the database',
            )
        doc = PatternDoc(
            slug=slug, title=title, file_path=rel_path,
            category='procedure',
            content_hash=c_hash,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = int(doc.id or 0)
    _patterns_log().write(
        'pattern_imported', slug=slug, title=title, doc_id=doc_id,
    )
    return doc_id


def _apply_manifest_tags(doc_id: int, tags: list[str]) -> None:
    """Attach tags from a bundle manifest to the newly-registered pattern.

    Tags that don't yet exist in the `tags` table are created with the
    default `concept` category (same fallback the /api/patterns/<slug>/tags
    endpoint uses when the UI submits an unknown tag).
    """
    if not tags:
        return
    with SessionLocal() as session:
        for raw in tags:
            name = str(raw).strip()
            if not name:
                continue
            tag = session.exec(
                select(Tag).where(Tag.name == name)
            ).first()
            if tag is None:
                tag = Tag(name=name, category='concept')
                session.add(tag)
                session.flush()  # populate tag.id

            link_exists = session.exec(
                select(DocTag).where(
                    DocTag.doc_id == doc_id, DocTag.tag_id == tag.id,
                )
            ).first()
            if link_exists is None:
                session.add(DocTag(doc_id=doc_id, tag_id=tag.id))
        session.commit()
    _patterns_log().write(
        "manifest_tags_applied",
        doc_id=doc_id, tag_count=len(tags), tags=tags,
    )


def _merge_grit_rules(root: str, slug: str) -> tuple[list[str], list[str], list[str]]:
    """Install any grit rules a bundle carries into the active grit_dir and
    wire them into the live rule pipeline.

    A promoted bundle ships `.grit/patterns/<lang>/*.grit` (see
    `pattern_promoter._collect_grit_rules`). This installs those sources into
    `grit_dir`, enables any language the grit engine isn't yet configured for,
    regenerates the rule index, and refreshes the deployed `grit-rules` skill.
    The rules land disabled until the pattern guide itself is deployed
    (`regin skills push --id <slug>`), matching the engine's guide-gating.

    Returns (rule_ids, grit_languages, enabled_languages); all empty when the
    import carries no grit sources. Best-effort: failures are logged and
    swallowed so a grit-merge problem never aborts the pattern import.
    """
    src_grit = os.path.join(root, '.grit')
    if not os.path.isdir(os.path.join(src_grit, 'patterns')):
        return [], [], []
    try:
        from lib.rules import grit_rule_index
        from lib.rule_engines import ensure_grit_languages
        from lib.skills.skill_deployer import deploy_rules_index_skill

        languages = grit_rule_index.install_grit_sources(
            src_grit, guide=slug,
        ).get('languages') or []
        if not languages:
            return [], [], []
        enabled = ensure_grit_languages(languages)
        grit_rule_index.refresh_language_dirs()
        grit_rule_index.regenerate(write_guides=False)
        deploy_rules_index_skill(grit_rule_index.RULES_MD_PATH)
        rule_ids = sorted(r['id'] for r in grit_rule_index.rules_for_guide(slug))
        _patterns_log().write(
            'grit_rules_merged', slug=slug, rules=rule_ids,
            languages=languages, enabled_languages=enabled,
        )
        return rule_ids, languages, enabled
    except Exception:
        _patterns_log().error('grit_rules_merge_failed', slug=slug, exc_info=True)
        return [], [], []


def _collapse_wrapper_dir(root: str) -> str:
    """If `root` contains exactly one (non-hidden) subdirectory and that
    subdir holds the SKILL.md, return the inner dir; otherwise return `root`
    unchanged."""
    entries = [e for e in os.listdir(root) if not e.startswith('.')]
    if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
        inner = os.path.join(root, entries[0])
        if os.path.isfile(os.path.join(inner, 'SKILL.md')):
            return inner
    return root


def _load_manifest(root: str) -> dict | None:
    """Load the optional manifest.json from `root`, or None if absent/unreadable."""
    manifest_path = os.path.join(root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_content_md(root: str) -> str | None:
    """Load the optional content.md from `root`, or None if absent."""
    content_md_path = os.path.join(root, 'content.md')
    if not os.path.isfile(content_md_path):
        return None
    with open(content_md_path, 'r', encoding='utf-8') as f:
        return f.read()


def _resolve_title(manifest: dict | None, fm: dict, derived_slug: str) -> str:
    """Prefer the bundle's explicit title (producer set it on promote);
    otherwise use the skill's own name."""
    manifest_title = (manifest or {}).get('title')
    if manifest_title:
        return str(manifest_title).strip()
    return _derive_title(fm, derived_slug)


def _prepare_pattern_dir(slug: str, force: bool) -> str:
    """Resolve the on-disk pattern dir for `slug`, clearing any existing one
    when `force` is set (else raising), then ensure the dir exists."""
    pattern_dir = os.path.join(str(settings.patterns_dir), slug)
    if os.path.exists(pattern_dir):
        if not force:
            raise ImportConflictError(
                f'patterns/{slug}/ already exists — pick a different name or '
                'remove the existing pattern first',
            )
        # Force: clean up old pattern on disk and in DB.
        shutil.rmtree(pattern_dir, ignore_errors=True)
        _remove_pattern_doc(slug)
    os.makedirs(pattern_dir, exist_ok=True)
    return pattern_dir


def _normalize_manifest_tags(manifest: dict | None) -> list[str]:
    """Extract manifest tags as a list, coercing a lone string to a 1-element
    list and missing/empty tags to []."""
    manifest_tags = (manifest or {}).get('tags') or []
    if isinstance(manifest_tags, str):
        manifest_tags = [manifest_tags]
    return list(manifest_tags)


def _import_from_dir(root: str, *, shape: str, force: bool,
                     target_slug: str | None) -> ImportResult:
    """Process a directory containing SKILL.md (+ optional manifest.json,
    content.md, and sibling files) into a regin pattern.

    Shared core between `import_zip` (extracts to a tmp dir, then calls
    this) and `import_skill_directory` (calls this directly).
    """
    # Collapse single top-level dir if the source was wrapped.
    root = _collapse_wrapper_dir(root)

    skill_md_path = os.path.join(root, 'SKILL.md')
    if not os.path.isfile(skill_md_path):
        raise ImportError_(f'missing SKILL.md at the top level of {root}')

    with open(skill_md_path, 'r', encoding='utf-8') as f:
        skill_md_raw = f.read()
    fm, skill_body = _parse_frontmatter(skill_md_raw)
    derived_slug = fm.get('name') or fm.get('procedure') or ''
    _validate_slug(derived_slug)

    # Load manifest (optional) to recover origin metadata.
    manifest = _load_manifest(root)

    # Prefer content.md over the shim body if both are present.
    body = _choose_body(skill_body, _load_content_md(root))

    title = _resolve_title(manifest, fm, derived_slug)

    slug = target_slug if target_slug else derived_slug
    _validate_slug(slug)

    pattern_dir = _prepare_pattern_dir(slug, force)

    # Write the pattern SKILL.md with regin frontmatter.
    out_skill_md = os.path.join(pattern_dir, 'SKILL.md')
    with open(out_skill_md, 'w', encoding='utf-8') as f:
        f.write(_build_pattern_skill_md(slug, title, body, manifest))

    # Copy everything else alongside (references/, scripts/, AND `.grit/`) —
    # but NOT the shim SKILL.md, content.md (already folded in), or
    # manifest.json (pattern format doesn't use it). `.grit/` is kept: it is
    # the deploy payload that lets the bundled `scripts/check_grit.sh` resolve
    # its sibling rules, and keeping it in the source dir means the deployed
    # skill (a copytree of this dir) stays byte-equal to the source — no false
    # drift. `_merge_grit_rules` separately installs the same rules into the
    # active grit_dir, which is canonical for the engine/index and re-promotion.
    exclude = {'SKILL.md', 'content.md', 'manifest.json'}
    extras = _copy_extras(root, pattern_dir, exclude)

    try:
        doc_id = _register_pattern_doc(slug, title, out_skill_md)
    except ImportConflictError:
        # This shouldn't happen after force cleanup, but if it does
        # (race condition), roll back the directory.
        shutil.rmtree(pattern_dir, ignore_errors=True)
        raise

    _apply_manifest_tags(doc_id, _normalize_manifest_tags(manifest))

    grit_rules, grit_languages, enabled_languages = _merge_grit_rules(root, slug)

    file_count = 1 + extras
    return ImportResult(
        slug=slug, title=title, pattern_dir=pattern_dir,
        shape=shape, file_count=file_count, doc_id=doc_id,
        grit_rules=grit_rules, grit_languages=grit_languages,
        enabled_languages=enabled_languages,
    )


def import_zip(zip_path: str, *, force: bool = False,
               target_slug: str | None = None) -> ImportResult:
    """Import a .zip bundle as a new pattern.

    Args:
        zip_path: Path to the .zip bundle.
        force: If True, overwrite an existing pattern with the same slug.
        target_slug: Use this slug instead of the one derived from SKILL.md
                     frontmatter. Useful for renaming on conflict.
    """
    if not os.path.isfile(zip_path):
        raise ImportError_(f'file not found: {zip_path}')
    if not zipfile.is_zipfile(zip_path):
        raise ImportError_(f'not a zip archive: {zip_path}')

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                # zip-slip guard
                if info.filename.startswith('/') or '..' in info.filename.split('/'):
                    raise ImportError_(
                        f'refusing archive with unsafe path: {info.filename}',
                    )
            zf.extractall(tmp)
        return _import_from_dir(
            tmp, shape='zip', force=force, target_slug=target_slug,
        )


def _safe_relpath(relpath: str) -> str:
    """Normalize an upload's relative path and reject anything that would
    escape the destination dir (absolute paths, `..` traversal)."""
    cleaned = (relpath or '').strip().replace('\\', '/').lstrip('/')
    if not cleaned:
        raise ImportError_('upload has an empty file path')
    parts = [p for p in cleaned.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        raise ImportError_(f'refusing unsafe upload path: {relpath!r}')
    if not parts:
        raise ImportError_(f'refusing unsafe upload path: {relpath!r}')
    return os.path.join(*parts)


def import_files(files: list[tuple[str, bytes]], *, force: bool = False,
                 target_slug: str | None = None) -> ImportResult:
    """Import a multi-file skill folder (uploaded from the browser) as a
    new pattern.

    Each entry is `(relative_path, content_bytes)` — e.g.
    `("my-skill/SKILL.md", b"...")`, `("my-skill/scripts/run.py", b"...")`.
    The files are written into a tempdir preserving structure, then handed
    to `_import_from_dir`, which collapses a single top-level wrapper dir,
    validates that SKILL.md exists, and copies every sibling file
    (`scripts/`, `references/`, …) alongside the rewritten pattern SKILL.md.

    Args:
        files: List of (relative_path, bytes) for every file in the folder.
        force: If True, overwrite an existing pattern with the same slug.
        target_slug: Use this slug instead of the one derived from SKILL.md.
    """
    if not files:
        raise ImportError_('no files in upload')

    with tempfile.TemporaryDirectory() as tmp:
        for relpath, content in files:
            dest = os.path.join(tmp, _safe_relpath(relpath))
            os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
            with open(dest, 'wb') as f:
                f.write(content)
        return _import_from_dir(
            tmp, shape='dir', force=force, target_slug=target_slug,
        )


def import_skill_directory(skill_dir: str, *, force: bool = False,
                           target_slug: str | None = None) -> ImportResult:
    """Import a directory containing a SKILL.md (Claude-skill layout) as a
    new pattern. Handles `references/`, `scripts/`, and any other sibling
    files the same way `import_zip` does for bundles.

    Args:
        skill_dir: Directory containing `SKILL.md` (e.g. one entry from
                   `~/.claude/skills/<name>/`).
        force: If True, overwrite an existing pattern with the same slug.
        target_slug: Use this slug instead of the one derived from frontmatter.
    """
    if not os.path.isdir(skill_dir):
        raise ImportError_(f'not a directory: {skill_dir}')
    return _import_from_dir(
        skill_dir, shape='dir', force=force, target_slug=target_slug,
    )


def import_skill_md(md_text: str | bytes, *, force: bool = False,
                    target_slug: str | None = None) -> ImportResult:
    """Import a bare SKILL.md file as a new pattern.

    Args:
        md_text: Raw SKILL.md content.
        force: If True, overwrite an existing pattern with the same slug.
        target_slug: Use this slug instead of the one derived from frontmatter.
    """
    if isinstance(md_text, (bytes, bytearray)):
        md_text = md_text.decode('utf-8')

    fm, body = _parse_frontmatter(md_text)
    derived_slug = fm.get('name') or fm.get('procedure') or ''
    _validate_slug(derived_slug)
    title = _derive_title(fm, derived_slug)

    slug = target_slug if target_slug else derived_slug
    _validate_slug(slug)

    pattern_dir = os.path.join(str(settings.patterns_dir), slug)
    if os.path.exists(pattern_dir):
        if not force:
            raise ImportConflictError(
                f'patterns/{slug}/ already exists — pick a different name or '
                'remove the existing pattern first',
            )
        shutil.rmtree(pattern_dir, ignore_errors=True)
        _remove_pattern_doc(slug)
    os.makedirs(pattern_dir, exist_ok=True)

    out_skill_md = os.path.join(pattern_dir, 'SKILL.md')
    with open(out_skill_md, 'w', encoding='utf-8') as f:
        f.write(_build_pattern_skill_md(slug, title, body))

    try:
        doc_id = _register_pattern_doc(slug, title, out_skill_md)
    except ImportConflictError:
        shutil.rmtree(pattern_dir, ignore_errors=True)
        raise

    return ImportResult(
        slug=slug, title=title, pattern_dir=pattern_dir,
        shape='skill-md', file_count=1, doc_id=doc_id,
    )


@dataclass
class BatchScanEntry:
    """One candidate `<name>/SKILL.md` directory found by scan_skill_directory."""
    name: str
    skill_dir: str
    derived_slug: str | None       # slug declared inside SKILL.md frontmatter
    conflict: bool                  # True if derived_slug already a pattern
    error: str | None              # non-null = scanned but unimportable


@dataclass
class BatchImportEntry:
    """One outcome row from batch_import_skill_directory."""
    name: str
    status: str       # 'imported' | 'skipped' | 'overwritten' | 'renamed' | 'failed' | 'planned'
    slug: str | None
    title: str | None
    file_count: int | None
    error: str | None
    grit_rules: list[str] = field(default_factory=list)
    grit_languages: list[str] = field(default_factory=list)
    enabled_languages: list[str] = field(default_factory=list)


def _slug_exists(slug: str) -> bool:
    """Cheap check: does either the pattern dir or DB row already exist?"""
    if not slug:
        return False
    if os.path.exists(os.path.join(str(settings.patterns_dir), slug)):
        return True
    with SessionLocal() as session:
        return session.exec(
            select(PatternDoc.id).where(PatternDoc.slug == slug)
        ).first() is not None


def _next_available_slug(base: str) -> str:
    """Pick the next free `base-N` slug not used on disk or in the DB."""
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if not _slug_exists(candidate):
            return candidate
    raise RuntimeError(f"too many slug collisions for {base!r}")


def scan_skill_directory(root_dir: str) -> list[BatchScanEntry]:
    """Discover `<root>/<name>/SKILL.md` candidates and flag slug conflicts.

    Returns one entry per child directory that contains a SKILL.md.
    Parsing errors (bad frontmatter, invalid slug) are reported on the
    entry's `error` field so the UI can show them rather than aborting.
    """
    if not os.path.isdir(root_dir):
        raise ImportError_(f'not a directory: {root_dir}')

    entries: list[BatchScanEntry] = []
    for name in sorted(os.listdir(root_dir)):
        if name.startswith('.'):
            continue
        skill_dir = os.path.join(root_dir, name)
        skill_md = os.path.join(skill_dir, 'SKILL.md')
        if not (os.path.isdir(skill_dir) and os.path.isfile(skill_md)):
            continue
        derived: str | None = None
        err: str | None = None
        try:
            with open(skill_md, 'r', encoding='utf-8') as f:
                fm, _ = _parse_frontmatter(f.read())
            derived = (fm.get('name') or fm.get('procedure') or '').strip() or None
            if derived:
                _validate_slug(derived)
        except ImportError_ as e:
            err = str(e)
        except OSError as e:
            err = f'cannot read SKILL.md: {e}'

        conflict = bool(derived and _slug_exists(derived))
        entries.append(BatchScanEntry(
            name=name, skill_dir=skill_dir, derived_slug=derived,
            conflict=conflict, error=err,
        ))
    return entries


def _grit_fields(result: ImportResult) -> dict:
    """The grit-merge fields of an ImportResult, ready to spread into a
    BatchImportEntry constructor."""
    return {
        'grit_rules': result.grit_rules,
        'grit_languages': result.grit_languages,
        'enabled_languages': result.enabled_languages,
    }


def _plan_entry(s: BatchScanEntry, on_conflict: str) -> BatchImportEntry:
    """Dry-run outcome row for one scanned candidate (status='planned')."""
    if s.conflict:
        planned_status = (
            'skipped' if on_conflict == 'skip'
            else ('overwritten' if on_conflict == 'overwrite' else 'renamed')
        )
    else:
        planned_status = 'imported'
    return BatchImportEntry(
        name=s.name, status='planned', slug=s.derived_slug,
        title=None, file_count=None,
        error=f'plan: {planned_status}',
    )


def _resolve_conflict(s: BatchScanEntry, on_conflict: str) -> BatchImportEntry:
    """Build the outcome row after `import_skill_directory` raised a conflict,
    applying the configured `on_conflict` policy (skip|overwrite|rename)."""
    if on_conflict == 'skip':
        return BatchImportEntry(
            name=s.name, status='skipped', slug=s.derived_slug,
            title=None, file_count=None,
            error='pattern already exists',
        )
    elif on_conflict == 'overwrite':
        try:
            result = import_skill_directory(s.skill_dir, force=True)
            return BatchImportEntry(
                name=s.name, status='overwritten', slug=result.slug,
                title=result.title, file_count=result.file_count,
                error=None, **_grit_fields(result),
            )
        except ImportError_ as e:
            return BatchImportEntry(
                name=s.name, status='failed', slug=s.derived_slug,
                title=None, file_count=None, error=str(e),
            )
    else:  # rename
        try:
            new_slug = _next_available_slug(s.derived_slug or s.name)
            result = import_skill_directory(
                s.skill_dir, target_slug=new_slug,
            )
            return BatchImportEntry(
                name=s.name, status='renamed', slug=result.slug,
                title=result.title, file_count=result.file_count,
                error=None, **_grit_fields(result),
            )
        except ImportError_ as e:
            return BatchImportEntry(
                name=s.name, status='failed', slug=s.derived_slug,
                title=None, file_count=None, error=str(e),
            )


def _import_one(s: BatchScanEntry, on_conflict: str) -> BatchImportEntry:
    """Import a single scanned candidate, returning its outcome row."""
    try:
        result = import_skill_directory(s.skill_dir)
        return BatchImportEntry(
            name=s.name, status='imported', slug=result.slug,
            title=result.title, file_count=result.file_count, error=None,
            **_grit_fields(result),
        )
    except ImportConflictError:
        return _resolve_conflict(s, on_conflict)
    except ImportError_ as e:
        return BatchImportEntry(
            name=s.name, status='failed', slug=s.derived_slug,
            title=None, file_count=None, error=str(e),
        )


def batch_import_skill_directory(
        root_dir: str, *,
        on_conflict: str = 'skip',
        dry_run: bool = False,
        progress=None,
) -> list[BatchImportEntry]:
    """Walk `<root>/<name>/SKILL.md` and import each as a pattern.

    Args:
        root_dir: Parent directory whose children are skill folders.
        on_conflict: 'skip' | 'overwrite' | 'rename'.
        dry_run: If True, just report what would happen (status='planned').
        progress: Optional callback `progress(BatchImportEntry)` after each
                  candidate so callers can stream output (e.g. the CLI).
    """
    if on_conflict not in {'skip', 'overwrite', 'rename'}:
        raise ImportError_(
            f'invalid on_conflict={on_conflict!r}; use skip|overwrite|rename',
        )
    scanned = scan_skill_directory(root_dir)
    out: list[BatchImportEntry] = []

    for s in scanned:
        if s.error:
            entry = BatchImportEntry(
                name=s.name, status='failed', slug=None, title=None,
                file_count=None, error=s.error,
            )
        elif dry_run:
            entry = _plan_entry(s, on_conflict)
        else:
            entry = _import_one(s, on_conflict)

        out.append(entry)
        if progress: progress(entry)

    return out


def import_upload(filename: str, data: bytes, *, force: bool = False,
                  target_slug: str | None = None) -> ImportResult:
    """Dispatch on extension.

    Args:
        filename: Original filename (used to determine type).
        data: Raw file bytes.
        force: If True, overwrite an existing pattern with the same slug.
        target_slug: Use this slug instead of the derived one.
    """
    low = (filename or '').lower()
    if low.endswith('.zip'):
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            return import_zip(tmp_path, force=force, target_slug=target_slug)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    if low.endswith('.md'):
        return import_skill_md(data, force=force, target_slug=target_slug)
    raise ImportError_(
        f'unsupported file type: {filename!r} '
        '(expected .zip or .md)',
    )
