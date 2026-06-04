"""Promote a pattern guide into a regin-skillhub skill bundle.

A promotion reads `patterns/<slug>/SKILL.md`, splits its body into a
shim + content.md, builds a `.zip` bundle (schema v1), and POSTs it
to the optional sibling `regin-skillhub` server's `/api/import` endpoint
(default `http://127.0.0.1:8322`, override via
`config/settings.local.json` → `skillhub_url`).

regin has no runtime dependency on regin-skillhub — this module
is lazy-imported only when the user explicitly runs `pattern promote`,
and the HTTP call fails cleanly with a clear error if the server is
not reachable.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid
import zipfile
from typing import Any

from lib.rules import grit_rule_index
from lib.settings import settings
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag

SCHEMA_VERSION = 1
DEFAULT_VERSION = '1.0.0'
_SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')
_VALID_NAME = re.compile(r'^[a-z][a-z0-9-]*$')

# Runner scripts shipped alongside a pattern when it bundles grit rules —
# same sources `skill_deployer.deploy_rules_index_skill` uses for the
# master `grit-rules` skill, copied verbatim so a deployed pattern
# skill can run its own checks without `grit-rules` installed.
_RUNNER_SCRIPTS = ('check_grit.sh', 'filter_grit_output.py', 'find_applicable_files.py')


class PromoteError(Exception):
    """Raised when promotion cannot proceed."""


def _resolve_url(override: str | None) -> str:
    url = ((override or settings.skillhub_url) or '').strip().rstrip('/')
    if not url:
        raise PromoteError('no skillhub_url configured')
    return url


def is_available(skillhub_url: str | None = None, timeout: float = 2.0) -> dict:
    """Ping the regin-skillhub server and report availability.

    Returns: {available: bool, url: str, reason: str | None}
    """
    try:
        url = _resolve_url(skillhub_url)
    except PromoteError as e:
        return {'available': False, 'url': '', 'reason': str(e)}

    health = f'{url}/api/health'
    try:
        req = urllib.request.Request(health, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return {'available': False, 'url': url,
                        'reason': f'/api/health returned HTTP {resp.status}'}
            body = json.loads(resp.read().decode('utf-8') or '{}')
            if body.get('service') != 'regin-skillhub':
                return {'available': False, 'url': url,
                        'reason': f'server at {url} is not regin-skillhub'}
            return {'available': True, 'url': url, 'reason': None}
    except urllib.error.URLError as e:
        return {'available': False, 'url': url,
                'reason': f'cannot reach {url}: {e.reason}'}
    except (json.JSONDecodeError, OSError, TimeoutError) as e:
        return {'available': False, 'url': url, 'reason': f'unexpected error: {e}'}


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + len('\n---\n'):]
    fm: dict[str, Any] = {}
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
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            if val.startswith('[') and val.endswith(']'):
                inner = val[1:-1].strip()
                fm[key.strip()] = [p.strip() for p in inner.split(',')] if inner else []
            else:
                fm[key.strip()] = val
            current = key.strip()
    return fm, body


def _read_pattern(slug: str) -> tuple[dict, str]:
    skill_md = os.path.join(str(settings.patterns_dir), slug, 'SKILL.md')
    if not os.path.isfile(skill_md):
        raise PromoteError(f'pattern {slug!r} has no SKILL.md at {skill_md}')
    with open(skill_md, 'r', encoding='utf-8') as f:
        raw = f.read()
    return _parse_frontmatter(raw)


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ['git', '-C', str(settings.project_root), 'rev-parse', 'HEAD'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return out
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'unknown'


def _strip_inline_frontmatter(body: str) -> str:
    """Remove a leading `--- ... ---` block even when no blank line
    separates it from the prose that follows."""
    lines = body.lstrip('\n').splitlines()
    i = 0
    # Skip leading headings / blank lines so we can find an inline
    # YAML block that appears after a title line.
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith('#')):
        i += 1
    if i < len(lines) and lines[i].strip() == '---':
        j = i + 1
        while j < len(lines) and lines[j].strip() != '---':
            j += 1
        if j < len(lines):  # found closing ---
            return '\n'.join(lines[:i] + lines[j + 1:])
    return body


def _derive_description(title: str, body: str) -> str:
    """Build a description from pattern title + first prose paragraph."""
    cleaned = _strip_inline_frontmatter(body)
    first_para = ''
    for para in cleaned.strip().split('\n\n'):
        text = para.strip()
        if not text or text.startswith('#'):
            continue
        first_para = text.replace('\n', ' ')
        break
    if not first_para:
        return f'{title} - procedure guide from regin'
    return f'{title}. {first_para}'


def _fetch_pattern_metadata(slug: str) -> tuple[str | None, list[str]]:
    """Read the pattern's title and tags from the regin DB.

    Returns (title, tags). Missing fields come back as None / []. The DB
    is authoritative for tags (assigned via the /api/patterns/<slug>/tags
    endpoint) and for the pattern's display title.
    """
    with SessionLocal() as session:
        doc = session.exec(
            select(PatternDoc).where(PatternDoc.slug == slug)
        ).first()
        if doc is None:
            return None, []
        tags = list(session.exec(
            select(Tag.name)
            .join(DocTag, DocTag.tag_id == Tag.id)
            .where(DocTag.doc_id == doc.id)
            .order_by(Tag.name)
        ).all())
        return doc.title, tags


def _collect_references(slug: str) -> dict[str, bytes]:
    refs: dict[str, bytes] = {}
    ref_root = os.path.join(str(settings.patterns_dir), slug, 'references')
    if not os.path.isdir(ref_root):
        return refs
    for root, _, files in os.walk(ref_root):
        for name in files:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, ref_root)
            with open(abs_path, 'rb') as f:
                refs[rel] = f.read()
    return refs


def _collect_pattern_scripts(slug: str) -> dict[str, bytes]:
    """Walk `patterns/<slug>/scripts/` and return {relpath: bytes}.

    Mirrors `_collect_references`. Used for any runnable helper the
    pattern author ships (Python, shell, node — no per-extension
    handling). Keys are relative to the scripts directory.
    """
    out: dict[str, bytes] = {}
    script_root = os.path.join(str(settings.patterns_dir), slug, 'scripts')
    if not os.path.isdir(script_root):
        return out
    for root, _, files in os.walk(script_root):
        for name in files:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, script_root)
            with open(abs_path, 'rb') as f:
                out[rel] = f.read()
    return out


def _collect_runner_scripts() -> dict[str, bytes]:
    """Read the shared grit runner scripts from the regin root.

    Returns {filename: bytes} for check_grit.sh and find_applicable_files.py.
    Only used when the pattern bundles grit rules.
    """
    out: dict[str, bytes] = {}
    for name in _RUNNER_SCRIPTS:
        path = os.path.join(str(settings.project_root), 'scripts', name)
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                out[name] = f.read()
    return out


def _collect_grit_rules(slug: str) -> tuple[dict[str, bytes], dict, list[str]]:
    """Gather every .grit source file and a trimmed rules.json for `slug`.

    Returns (grit_files, rules_json, rule_ids) where:
      - grit_files: {relpath-under-.grit/: bytes} for every source_file
        that contains a rule whose guide matches `slug`. Whole files are
        copied — a single .grit file may contain several rules, all
        sharing the same guide in practice.
      - rules_json: a subset of `.grit/rules.json` structured exactly
        like the original (rules + by_layer/by_trigger/by_guide) so the
        deployed skill can hand it to the same tooling.
      - rule_ids: sorted list of rule IDs included (for the manifest).
    """
    rules = grit_rule_index.rules_for_guide(slug)
    if not rules:
        return {}, {}, []

    grit_files: dict[str, bytes] = {}
    for rule in rules:
        src_rel = rule.get('source_file')
        if not src_rel:
            continue
        abs_path = os.path.join(str(settings.project_root), src_rel)
        if not os.path.isfile(abs_path):
            continue
        # Store under the bundle's `.grit/` layout, i.e. strip the
        # leading `.grit/` segment from the PROJECT_ROOT-relative
        # source_file that grit_parser records.
        rel_under_grit = os.path.relpath(
            abs_path, os.path.join(str(settings.project_root), '.grit'),
        )
        if rel_under_grit in grit_files:
            continue
        with open(abs_path, 'rb') as f:
            grit_files[rel_under_grit] = f.read()

    by_layer: dict[str, list[str]] = {}
    by_trigger: dict[str, list[str]] = {}
    by_guide: dict[str, list[str]] = {}
    for r in rules:
        by_layer.setdefault(r['layer'], []).append(r['id'])
        by_guide.setdefault(r['guide'], []).append(r['id'])
        for trig in r.get('triggers', []):
            by_trigger.setdefault(trig, []).append(r['id'])
    rules_json = {
        'version': 1,
        'rules': rules,
        'by_layer': {k: sorted(v) for k, v in sorted(by_layer.items())},
        'by_trigger': {k: sorted(v) for k, v in sorted(by_trigger.items())},
        'by_guide': {k: sorted(v) for k, v in sorted(by_guide.items())},
    }
    rule_ids = sorted(r['id'] for r in rules)
    return grit_files, rules_json, rule_ids


def _compute_checksum(skill_md: str, content_md: str,
                      references: dict[str, bytes],
                      extras: dict[str, bytes] | None = None) -> str:
    h = hashlib.sha256()
    h.update(skill_md.encode('utf-8'))
    h.update(b'\x00')
    h.update(content_md.encode('utf-8'))
    h.update(b'\x00')
    for name in sorted(references):
        h.update(name.encode('utf-8'))
        h.update(b'\x00')
        h.update(references[name])
        h.update(b'\x00')
    if extras:
        # Separator domain so a reference named "x" and an extra named
        # "x" can never collide into the same hash state.
        h.update(b'\x01')
        for name in sorted(extras):
            h.update(name.encode('utf-8'))
            h.update(b'\x00')
            h.update(extras[name])
            h.update(b'\x00')
    return f'sha256:{h.hexdigest()}'


def _as_str_list(value: Any) -> list:
    """Normalize a frontmatter value that may be a scalar str or a list.

    A bare string becomes a single-element list; ``None``/falsey becomes ``[]``.
    """
    value = value or []
    if isinstance(value, str):
        return [value]
    return value


def _resolve_tags(db_tags: list[str], fm: dict) -> list[str]:
    """Pick the tag list for the manifest.

    DB is the source of truth (managed via the /patterns/<slug>/tags
    endpoint); frontmatter tags are a fallback for patterns whose DB
    row hasn't been tagged yet.
    """
    if db_tags:
        return db_tags
    return [str(t).strip() for t in _as_str_list(fm.get('tags')) if str(t).strip()]


def _assemble_extras(scripts_payload: dict[str, bytes], rule_ids: list[str],
                     grit_files: dict[str, bytes], rules_json: dict) -> dict[str, bytes]:
    """Build the {bundle-relpath: bytes} map of script + grit-rule extras."""
    extras: dict[str, bytes] = {}
    for name, data in sorted(scripts_payload.items()):
        extras[f'scripts/{name}'] = data
    if rule_ids:
        extras['.grit/rules.json'] = (
            json.dumps(rules_json, indent=2, sort_keys=False).encode('utf-8') + b'\n'
        )
        for rel, data in grit_files.items():
            extras[f'.grit/{rel}'] = data
    return extras


def _write_bundle_zip(manifest: dict, shim_md: str, content_md: str,
                      references: dict[str, bytes], extras: dict[str, bytes]) -> bytes:
    """Serialize the bundle contents into a deflated zip and return its bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, indent=2, sort_keys=True))
        zf.writestr('SKILL.md', shim_md)
        zf.writestr('content.md', content_md)
        for rel, data in references.items():
            zf.writestr(f'references/{rel}', data)
        for rel, data in sorted(extras.items()):
            zf.writestr(rel, data)
    return buf.getvalue()


def build_bundle(slug: str, version: str = DEFAULT_VERSION,
                 author: str | None = None) -> tuple[str, bytes]:
    """Produce (filename, bytes) for the `.zip` bundle.

    Does not touch regin-skillhub. Useful for unit tests or if the caller
    wants to manage the delivery themselves.
    """
    if not _VALID_NAME.match(slug):
        raise PromoteError(f'invalid pattern slug: {slug!r} (must match [a-z][a-z0-9-]*)')
    if not _SEMVER_RE.match(version):
        raise PromoteError(f'invalid version: {version!r} (expected MAJOR.MINOR.PATCH)')

    fm, body = _read_pattern(slug)
    db_title, db_tags = _fetch_pattern_metadata(slug)
    # `description_seed` is the (often descriptive) frontmatter title used
    # only to prime `_derive_description`. It is NOT used as the manifest
    # title — see below.
    description_seed = db_title or fm.get('title') or slug
    description = _derive_description(str(description_seed), body)
    safe_description = description.replace('"', '\\"')

    # The manifest `title` is a short display name. Default to the skill
    # slug; an author who wants a different label must set `display_title`
    # in the pattern's frontmatter. We deliberately do *not* reuse
    # `title` / `pattern_docs.title` because those fields are populated
    # by the sync pipeline with description-like text, not display names.
    title = fm.get('display_title') or slug
    tags = _resolve_tags(db_tags, fm)

    shim_md = (
        f'---\n'
        f'name: {slug}\n'
        f'description: "{safe_description}"\n'
        f'---\n'
        f'\n'
        f'CRITICAL: Before using any guidance from this skill, read the full procedure from:\n'
        f'`~/.claude/skills/{slug}/content.md`\n'
        f'\n'
        f'Do not act on partial knowledge. Always read `content.md` first, then follow its instructions.\n'
    )
    content_md = body.lstrip('\n')
    references = _collect_references(slug)

    # Gather extras: pattern scripts, grit rules, plus the runner scripts
    # (only shipped when the pattern has any rules to run). Pattern
    # scripts win on name collisions — the runner list is a default the
    # author can override.
    pattern_scripts = _collect_pattern_scripts(slug)
    grit_files, rules_json, rule_ids = _collect_grit_rules(slug)

    scripts_payload: dict[str, bytes] = {}
    if rule_ids:
        scripts_payload.update(_collect_runner_scripts())
    scripts_payload.update(pattern_scripts)  # pattern overrides runner

    extras = _assemble_extras(scripts_payload, rule_ids, grit_files, rules_json)

    manifest: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'name': slug,
        'title': str(title),
        'version': version,
        'description': description,
        'author': author or os.environ.get('USER', 'unknown'),
        'license': 'internal',
        'tags': tags,
        'dependencies': [],
        'origin': {
            'type': 'promoted-from-pattern',
            'source_repo': 'regin',
            'source_path': f'patterns/{slug}',
            'source_commit': _git_head(),
            'source_repos': _as_str_list(fm.get('source_repos')),
            'created_at': _dt.datetime.now().isoformat(timespec='seconds'),
        },
    }
    if rule_ids or scripts_payload:
        manifest['bundled'] = {
            'grit_rules': rule_ids,
            'scripts': sorted(scripts_payload.keys()),
        }
    manifest['checksum'] = _compute_checksum(shim_md, content_md, references, extras)

    data = _write_bundle_zip(manifest, shim_md, content_md, references, extras)
    return f'{slug}-{version}.zip', data


def _build_multipart(filename: str, data: bytes, force: bool) -> tuple[bytes, str]:
    """Return (body_bytes, content_type) for a multipart/form-data POST."""
    boundary = f'----skillbundle{uuid.uuid4().hex}'
    lines: list[bytes] = []

    # bundle file part
    lines.append(f'--{boundary}\r\n'.encode())
    lines.append(
        f'Content-Disposition: form-data; name="bundle"; filename="{filename}"\r\n'
        .encode()
    )
    lines.append(b'Content-Type: application/zip\r\n\r\n')
    lines.append(data)
    lines.append(b'\r\n')

    # force field
    lines.append(f'--{boundary}\r\n'.encode())
    lines.append(b'Content-Disposition: form-data; name="force"\r\n\r\n')
    lines.append(b'true' if force else b'false')
    lines.append(b'\r\n')

    lines.append(f'--{boundary}--\r\n'.encode())
    body = b''.join(lines)
    return body, f'multipart/form-data; boundary={boundary}'


def _post_bundle(url: str, filename: str, data: bytes, force: bool,
                 timeout: float = 30.0) -> dict:
    body, content_type = _build_multipart(filename, data, force)
    req = urllib.request.Request(
        f'{url}/api/import',
        data=body,
        headers={'Content-Type': content_type,
                 'Content-Length': str(len(body))},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8') or '{}'
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8') if e.fp else ''
        try:
            detail = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            detail = {'raw': raw}
        raise PromoteError(
            f'regin-skillhub rejected bundle (HTTP {e.code}): '
            f'{detail.get("error") or raw or e.reason}',
        )
    except urllib.error.URLError as e:
        raise PromoteError(f'cannot reach regin-skillhub at {url}: {e.reason}')


def promote(slug: str, version: str = DEFAULT_VERSION,
            skillhub_url: str | None = None,
            force: bool = False) -> dict:
    """End-to-end promotion: build bundle → POST to regin-skillhub.

    Returns {slug, version, bundle_filename, url, response}.
    """
    url = _resolve_url(skillhub_url)
    filename, data = build_bundle(slug, version=version)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        bundle_manifest = json.loads(zf.read('manifest.json').decode('utf-8'))

    result = _post_bundle(url, filename, data, force)
    if not result.get('ok'):
        err = result.get('error') or 'import failed'
        raise PromoteError(f'regin-skillhub import failed: {err}')

    from lib.activity_log import get_activity_logger
    get_activity_logger('patterns').write(
        'pattern_promoted', slug=slug, version=version,
        bundle_filename=filename, skillhub_url=url,
    )
    return {
        'slug': slug,
        'version': version,
        'bundle_filename': filename,
        'url': url,
        'response': result,
        'bundled': bundle_manifest.get('bundled') or {},
    }
