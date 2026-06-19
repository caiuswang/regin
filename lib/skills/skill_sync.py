"""Pull / push / check managed skills (see lib/skill_registry)."""

import hashlib
import os
import re
import shutil

from lib.skills import skill_registry
from lib.providers import get_active_provider
from lib.skills.skill_registry import (
    deployed_exists,
    deployed_path,
    deployed_skill_md,
    source_exists,
    source_path,
    source_skill_md,
)

STATE_IN_SYNC = 'in_sync'
STATE_DRIFTED = 'drifted'
STATE_DEPLOYED_ONLY = 'deployed_only'
STATE_SOURCE_ONLY = 'source_only'
STATE_PROJECT_ONLY = 'project_only'
STATE_MISSING = 'missing'


def _has_project_deployment(pattern_slug):
    """Return True if any non-global PatternDeployment row exists for the slug.

    Kept local to avoid an import cycle at module load — pattern_deployments
    only needs to be touched on the source-without-global path.
    """
    from lib.patterns import pattern_deployments
    rows = pattern_deployments.list_deployments(pattern_slug=pattern_slug)
    return any(r.get('scope') == 'project' for r in rows)


def _provider_supports_skills() -> tuple[bool, str]:
    provider = get_active_provider()
    return provider.capabilities.skills, provider.display_name


# ---------- hashing -------------------------------------------------------

def _hash_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------- pattern section helpers ---------------------------------------

_SECTION_RE = r'\n{header}\s*\n(.*?)(?=\n## |\Z)'


def _extract_section(content, header):
    m = re.search(_SECTION_RE.format(header=re.escape(header)),
                  content, re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_disciplines_and_anti(md_text):
    return (
        _extract_section(md_text, '## Disciplines'),
        _extract_section(md_text, '## Anti-Patterns'),
    )


def _replace_section(content, header, new_body):
    """Replace the body of an existing `## Header` section.

    If the header is not present, returns the content unchanged.
    """
    pattern = re.compile(
        r'(\n' + re.escape(header) + r'\s*\n)(.*?)(?=\n## |\Z)',
        re.DOTALL,
    )
    return pattern.sub(lambda m: m.group(1) + new_body.rstrip() + '\n', content, count=1)


def _pattern_extras_hash(root, ignore_top_level=frozenset()):
    """Stat-based fingerprint of everything under `root` except SKILL.md,
    content.md, and any top-level dir in `ignore_top_level` (deploy-time
    attached engine bundles, which live only on the deployed side and would
    otherwise read as drift). Combines each file's relative path, size, and
    mtime instead
    of its sha256 content hash — eliminates per-file reads while preserving
    drift detection for the deploy → edit workflow (shutil.copytree's copy2
    preserves mtime, so source and deployed extras match right after a
    deploy and diverge only when the source is edited).

    Edge case: a content-preserving edit that leaves both size AND mtime
    untouched is undetectable. That's vanishingly rare for the editor
    workflows regin targets; the previous sha256 walk was the dominant cost
    of `GET /api/patterns`, so the trade-off is intentional.
    """
    if not os.path.isdir(root):
        return None
    h = hashlib.sha256()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if rel in ('SKILL.md', 'content.md'):
                continue
            if ignore_top_level and rel.split(os.sep, 1)[0] in ignore_top_level:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            h.update(rel.encode('utf-8'))
            h.update(b'\0')
            h.update(f'{st.st_size}:{st.st_mtime_ns}'.encode('ascii'))
            h.update(b'\0')
    return h.hexdigest()


def _pattern_signature(md_text):
    """Canonical representation of the manually-owned zones of a pattern file.

    The exemplar code block is auto-synced from exemplars and must NOT be
    compared for drift.
    """
    disc, anti = _extract_disciplines_and_anti(md_text)
    return (disc or '', anti or '')


def _skill_body(md_text):
    """Strip the YAML frontmatter, return the body only."""
    parts = md_text.split('---', 2)
    return parts[2] if len(parts) >= 3 else md_text


def _deployed_body(skill_id):
    """Return the deployed skill body, reading content.md if present,
    otherwise falling back to SKILL.md for backward compatibility."""
    content_path = os.path.join(deployed_path(skill_id), 'content.md')
    if os.path.isfile(content_path):
        with open(content_path) as f:
            return f.read()
    with open(deployed_skill_md(skill_id)) as f:
        return _skill_body(f.read())


# ---------- public API ----------------------------------------------------

def _state_for_pattern(skill_id):
    """Drifted/in-sync check for type='pattern' skills (both sides exist)."""
    with open(source_skill_md(skill_id)) as f:
        src_sig = _pattern_signature(f.read())
    dep_sig = _pattern_signature(_deployed_body(skill_id))
    if src_sig != dep_sig:
        return STATE_DRIFTED
    if _pattern_extras_hash(source_path(skill_id)) != \
            _pattern_extras_hash(deployed_path(skill_id)):
        return STATE_DRIFTED
    return STATE_IN_SYNC


def _state_for_rules_index_auto(skill_id):
    """Drift check for the grit-rules auto-skill: compare deployed body to
    the local .grit/RULES.md. Missing RULES.md ⇒ treat as in-sync (we have
    a valid deployment with no baseline to verify against)."""
    from lib.settings import settings
    rules_md = os.path.join(str(settings.project_root), '.grit', 'RULES.md')
    if not os.path.isfile(rules_md):
        return STATE_IN_SYNC
    with open(rules_md) as f:
        rules_body = f.read().strip()
    return STATE_IN_SYNC if rules_body in _deployed_body(skill_id) else STATE_DRIFTED


def _state_for_auto(skill_id, entry):
    """Auto-skill dispatch by engine kind."""
    return _state_for_rules_index_auto(skill_id)


def state(skill_id, *, entry: dict | None = None):
    """Return one of STATE_* describing source vs deployed for the given skill.

    `entry` may be passed in to skip the internal `skill_registry.get(skill_id)`
    lookup — useful for bulk callers (e.g. the patterns list endpoint) that
    have already built a registry snapshot. When omitted, falls back to the
    per-call lookup for backward compatibility.
    """
    if entry is None:
        entry = skill_registry.get(skill_id)
    t = entry['type']
    src_exists = source_exists(skill_id)
    dep_exists = deployed_exists(skill_id)

    if not src_exists and not dep_exists:
        return STATE_MISSING
    if not src_exists:
        return STATE_DEPLOYED_ONLY
    if not dep_exists:
        if t == 'pattern' and _has_project_deployment(skill_id):
            return STATE_PROJECT_ONLY
        return STATE_SOURCE_ONLY
    if t == 'pattern':
        return _state_for_pattern(skill_id)
    return _state_for_auto(skill_id, entry)

    raise ValueError(f"unknown skill type: {t}")


def list_states():
    """Yield (skill_id, type, source_path, deployed_path, state) tuples."""
    for skill_id in skill_registry.all_ids():
        entry = skill_registry.get(skill_id)
        yield (
            skill_id,
            entry['type'],
            source_path(skill_id),
            deployed_path(skill_id),
            state(skill_id),
        )


# ---------- rule cascade --------------------------------------------------

def _set_linked_rules_disabled(skill_id, disabled):
    """For pattern-type skills, disable/enable every rule whose @rule guide
    metadata points at the pattern. Returns the list of rule ids touched
    (empty for non-pattern skills or patterns with no rules).
    """
    entry = skill_registry.get(skill_id)
    if entry.get('type') != 'pattern':
        return []
    from lib.rules import grit_rule_index
    rules = grit_rule_index.rules_for_guide(entry['procedure_id'])
    if not rules:
        return []
    ids = [r['id'] for r in rules]
    grit_rule_index.set_rules_disabled(ids, disabled)
    grit_rule_index.regenerate(write_guides=False)
    # Refresh the deployed grit-rules skill so the agent's rule catalog
    # reflects the new enforcement state.
    from lib.skills.skill_deployer import deploy_rules_index_skill
    deploy_rules_index_skill(grit_rule_index.RULES_MD_PATH)
    return ids


# ---------- undeploy ------------------------------------------------------

def undeploy(skill_id, target_dir=None, provider_id=None,
             disable_linked_rules=True):
    """Remove global deployed skill and, for pattern skills, also
    disable every rule linked to the procedure guide so the PostToolUse
    hook stops reporting violations for a pattern the agent no longer has.

    ``target_dir`` overrides the provider's global skills dir. ``provider_id``
    is used for the status message and capability check when ``target_dir``
    is supplied.
    """
    from lib.providers import build_provider
    provider = build_provider(provider_id) if provider_id else get_active_provider()
    if not provider.capabilities.skills:
        return f"refused: skill deployment is not supported for {provider.display_name}"
    from lib.skills.skill_deployer import undeploy_skill as _undeploy
    removed = _undeploy(skill_id, target_dir=target_dir)
    if disable_linked_rules:
        disabled = _set_linked_rules_disabled(skill_id, True)
    else:
        disabled = []
    skills_dir = target_dir or provider.global_skills_dir()
    if not removed:
        return f"{skill_id} was not deployed"
    if disabled:
        return f"removed {skill_id} from {skills_dir} + disabled {len(disabled)} linked rule(s)"
    return f"removed {skill_id} from {skills_dir}"


# ---------- pull ----------------------------------------------------------

def pull(skill_id):
    """Copy the deployed skill into the regin source tree.

    Returns a short status string describing what was done.
    """
    ok, provider_name = _provider_supports_skills()
    if not ok:
        return f"refused: skill deployment is not supported for {provider_name}"
    entry = skill_registry.get(skill_id)
    t = entry['type']

    if t == 'auto':
        return (
            f"refused: {skill_id} is auto-generated — "
            f"run `.venv/bin/python cli/regin.py rules deploy` to refresh it."
        )

    if not deployed_exists(skill_id):
        return f"skipped: {skill_id} is not deployed at {deployed_path(skill_id)}"

    if t == 'pattern':
        return _pull_pattern(skill_id)

    raise ValueError(f"unknown skill type: {t}")


def _pull_pattern(skill_id):
    src_md = source_skill_md(skill_id)
    src_dir = source_path(skill_id)
    if not os.path.isfile(src_md):
        return (
            f"skipped: {skill_id} has no pattern guide at {src_md}. "
            f"Create the pattern via /patterns/new in the web UI first."
        )

    skill_text = _deployed_body(skill_id)
    disc, anti = _extract_disciplines_and_anti(skill_text)

    # Copy extra assets (references/, scripts/, ...) from deployed back to source.
    extras_changed = _mirror_pattern_extras(deployed_path(skill_id), src_dir)

    if disc is None and anti is None and not extras_changed:
        return f"noop: {skill_id} — nothing to pull"

    with open(src_md) as f:
        pattern_text = f.read()

    changed = False
    if disc is not None:
        new_text = _replace_section(pattern_text, '## Disciplines', disc + '\n')
        if new_text != pattern_text:
            pattern_text = new_text
            changed = True
    if anti is not None:
        new_text = _replace_section(pattern_text, '## Anti-Patterns', anti + '\n')
        if new_text != pattern_text:
            pattern_text = new_text
            changed = True

    if changed:
        with open(src_md, 'w') as f:
            f.write(pattern_text)

    parts = []
    if changed:
        parts.append('Disciplines/Anti-Patterns')
    if extras_changed:
        parts.append(f'{extras_changed} extra file(s)')
    if not parts:
        return f"noop: {skill_id} already in sync"
    return f"pulled pattern {skill_id} -> {src_dir} ({', '.join(parts)} updated)"


def _mirror_pattern_extras(deployed_dir, source_dir):
    """Mirror every non-SKILL.md / non-content.md file from `deployed_dir`
    into `source_dir`.

    Returns the number of files that were added / updated / removed.
    """
    if not os.path.isdir(deployed_dir):
        return 0

    changed = 0
    deployed_rels = set()
    for dirpath, _dirnames, filenames in os.walk(deployed_dir):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, deployed_dir)
            if rel in ('SKILL.md', 'content.md'):
                continue
            deployed_rels.add(rel)
            dst = os.path.join(source_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.isfile(dst) or _hash_file(full) != _hash_file(dst):
                shutil.copy2(full, dst)
                changed += 1

    # Remove source files that no longer exist in deployed.
    if os.path.isdir(source_dir):
        for dirpath, _dirnames, filenames in os.walk(source_dir):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, source_dir)
                if rel in ('SKILL.md', 'content.md'):
                    continue
                if rel not in deployed_rels:
                    os.remove(full)
                    changed += 1

    return changed


# ---------- push ----------------------------------------------------------

def push(skill_id, force=False, target_dir=None):
    """Copy the source into a skills directory.

    When `target_dir` is None, deploys to the global `~/.claude/skills/`
    (the default, with drift protection against direct edits).
    When `target_dir` is given (e.g. `<repo>/.claude/skills/`), deploys
    there instead; drift check is skipped because project deployments are
    treated as downstream copies that get overwritten on each push.

    Refuses the global push when the deployed copy differs from source,
    unless `force=True`. Source is never modified.
    """
    ok, provider_name = _provider_supports_skills()
    if not ok:
        return f"refused: skill deployment is not supported for {provider_name}"
    entry = skill_registry.get(skill_id)
    t = entry['type']

    if t == 'auto':
        if target_dir is not None:
            return f"skipped: {skill_id} is an auto skill and cannot be pushed to a project"
        if skill_id == 'grit-rules':
            from lib.rules import grit_rule_index
            from lib.skills.skill_deployer import deploy_rules_index_skill
            summary = grit_rule_index.regenerate(write_guides=True)
            path = deploy_rules_index_skill(summary['rules_md'])
            return f"pushed auto {skill_id} -> {path}"
        if skill_id == 'python-complexity':
            from lib.skills.skill_deployer import deploy_python_complexity_skill
            path = deploy_python_complexity_skill()
            return f"pushed auto {skill_id} -> {path}"
        return f"skipped: unknown auto skill generator for {skill_id}"

    if not source_exists(skill_id):
        return f"skipped: {skill_id} has no source at {source_path(skill_id)}"

    # Drift check is only meaningful for the global deployment.
    if target_dir is None:
        current = state(skill_id)
        if current == STATE_DRIFTED and not force:
            from lib import experiments
            pid = entry.get('procedure_id')
            has_experiment = pid and experiments.get_active(pid)
            reason = "an active concealment experiment" if has_experiment else "unmerged edits in the deployed copy"
            return (
                f"confirm-force: {skill_id} is drifted due to {reason}. "
                f"Force push will overwrite the deployed version. Source is not affected."
            )

    if t == 'pattern':
        from lib.skills.skill_deployer import deploy_pattern_as_skill
        title = _read_title(source_skill_md(skill_id)) or skill_id
        path = deploy_pattern_as_skill(source_path(skill_id), skill_id, title, target_dir=target_dir)
        extra = ""
        if target_dir is None:
            # Global push: re-enable the pattern's linked rules in the index.
            re_enabled = _set_linked_rules_disabled(skill_id, False)
            if re_enabled:
                extra = f" + re-enabled {len(re_enabled)} linked rule(s)"
        else:
            # Project push: also install the pattern's grit rules into the
            # target repo's `.grit/` so the repo-local PostToolUse hook
            # enforces them (the skill copy alone doesn't enable enforcement).
            extra = _sync_pattern_grit_to_project(skill_id, target_dir)
        return f"pushed pattern {skill_id} -> {path}{extra}"

    raise ValueError(f"unknown skill type: {t}")


def _sync_pattern_grit_to_project(skill_id, target_dir) -> str:
    """Install a project-deployed pattern's grit rules into the target repo's
    `.grit/` so the repo-local PostToolUse hook enforces them. Best-effort —
    a failure here never blocks the skill deployment. Returns a status suffix
    for the push message ("" when the pattern ships no grit rules)."""
    try:
        repo_root = target_dir
        for _ in get_active_provider().project_skills_subpath():
            repo_root = os.path.dirname(repo_root)
        from lib.rules import grit_rule_index
        res = grit_rule_index.sync_guide_rules_to_repo(skill_id, repo_root)
        if res.get('rules'):
            return f" + synced {res['rules']} grit rule(s) to {repo_root}/.grit"
        return ""
    except Exception:
        from lib.logging_setup import get_logger
        get_logger(__name__).warning(
            "grit_sync_to_project_failed", skill_id=skill_id, exc_info=True,
        )
        return ""


def _read_title(pattern_file):
    try:
        with open(pattern_file) as f:
            for line in f:
                m = re.match(r'title:\s*"?([^"\n]+)"?', line.strip())
                if m:
                    return m.group(1).strip()
    except IOError:
        pass
    return None
