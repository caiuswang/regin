"""Deploy pattern guides as skills for the active agent provider."""

import os
import re
import shutil

import yaml

from lib.settings import settings
from lib.providers import get_active_provider

_VALID_ID = re.compile(r'^[a-z0-9][a-z0-9-]*$')
_DEFAULT_SKILLS_DIR = str(settings.skills_dir)


def _validate_id(procedure_id):
    if not _VALID_ID.match(procedure_id):
        raise ValueError(f"Invalid procedure ID: {procedure_id}")


def _resolve_base(target_dir) -> str:
    """Return the skills base directory for deploy operations."""
    if target_dir:
        return target_dir
    # Back-compat: tests monkey-patch settings.skills_dir.
    if str(settings.skills_dir) != _DEFAULT_SKILLS_DIR:
        return str(settings.skills_dir)
    return str(get_active_provider().global_skills_dir())


def get_skill_path(procedure_id, target_dir=None):
    _validate_id(procedure_id)
    return os.path.join(_resolve_base(target_dir), procedure_id, 'SKILL.md')


def is_deployed(procedure_id, target_dir=None):
    try:
        return os.path.exists(get_skill_path(procedure_id, target_dir))
    except ValueError:
        return False


def get_deployed_procedures(target_dir=None):
    """Return set of procedure IDs that have a SKILL.md in the skills dir."""
    deployed = set()
    base = _resolve_base(target_dir)
    if not os.path.isdir(base):
        return deployed
    for name in os.listdir(base):
        skill_file = os.path.join(base, name, 'SKILL.md')
        if os.path.isfile(skill_file):
            deployed.add(name)
    return deployed


def deploy_pattern_as_skill(pattern_source_dir, procedure_id, title, target_dir=None):
    """Deploy a pattern directory as a skill for the active agent provider.

    A pattern source directory is laid out like any other skill:

        patterns/<procedure_id>/
            SKILL.md           # guide body (with regin frontmatter)
            references/        # optional — copied as-is
            scripts/           # optional — copied as-is
            ...

    This function copies the entire directory to `<target_dir>/<procedure_id>/`
    (defaulting to the active provider global dir when `target_dir` is None) and rewrites
    only the `SKILL.md` frontmatter into the provider-neutral skill format
    (`name` + `description`). All sibling files/directories are copied
    verbatim so patterns can ship references, scripts, etc. alongside the
    guide. Returns the path to the deployed `SKILL.md`.

    Pass `target_dir` to deploy into a project-local skills directory
    (e.g. `<repo>/.kimi-code/skills/`) instead of the global one.
    """
    _validate_id(procedure_id)

    src_skill_md = os.path.join(pattern_source_dir, 'SKILL.md')
    if not os.path.isfile(src_skill_md):
        raise FileNotFoundError(
            f"Pattern source {pattern_source_dir} is missing SKILL.md"
        )

    base = _resolve_base(target_dir)
    os.makedirs(base, exist_ok=True)
    skill_dir = os.path.join(base, procedure_id)
    # Rebuild destination from scratch so removed references don't linger.
    if os.path.isdir(skill_dir):
        shutil.rmtree(skill_dir)
    shutil.copytree(pattern_source_dir, skill_dir)

    # Rewrite the frontmatter into the provider-neutral skill format
    # (name + description) and keep the full guide body inline in SKILL.md.
    # The Skill tool loads
    # this body directly on invocation, so there is no separate content.md hop
    # to skip — invocation is captured by the skill_launch hook instead. (The
    # old shim + content.md split lost the body ~50% of the time, when the
    # model invoked the skill but never followed the "read content.md" pointer.)
    deployed_skill_md = os.path.join(skill_dir, 'SKILL.md')
    with open(deployed_skill_md, 'r') as f:
        content = f.read()
    parts = content.split('---', 2)
    body = parts[2] if len(parts) >= 3 else content

    # If a concealment experiment is active on this pattern, strip the
    # configured H2 sections before the guide ships. The `experiments`
    # module is imported lazily to avoid a circular import at module load.
    from lib import experiments
    active = experiments.get_active(procedure_id)
    if active:
        body = experiments.apply_conceal(body, active[1])

    frontmatter = _parse_simple_frontmatter(content)
    description = frontmatter.get("description") or f"{title} - procedure guide from regin"
    # description is a single-line double-quoted scalar, so any embedded
    # newlines from YAML block scalars must be collapsed first.
    description = re.sub(r"\s+", " ", str(description)).strip()
    description = description.replace('"', '\\"')
    skill_content = (
        f"---\n"
        f"name: {procedure_id}\n"
        f"description: \"{description}\"\n"
        f"---\n\n"
        f"{body.lstrip(chr(10))}"
    )
    with open(deployed_skill_md, 'w') as f:
        f.write(skill_content)

    from lib.activity_log import get_activity_logger
    get_activity_logger('patterns').write(
        'skill_deployed', procedure_id=procedure_id, path=str(deployed_skill_md),
    )
    return deployed_skill_md


def _display_path(abs_path):
    """Return a human-friendly path, collapsing $HOME to `~`."""
    home = os.path.expanduser('~')
    if abs_path == home or abs_path.startswith(home + os.sep):
        return '~' + abs_path[len(home):]
    return abs_path


RULES_INDEX_SKILL_ID = 'grit-rules'


def rules_index_description() -> str:
    """Derive the grit-rules skill description from the configured grit
    engine's `language_ids`. Falls back to Java when no grit engine is
    configured.
    """
    try:
        from lib.rule_engines import get as _get_engine
        langs = list(_get_engine('grit').language_ids) or ['java']
    except Exception:
        langs = ['java']
    pretty = '/'.join(langs)
    return (
        f'Use when writing or editing {pretty} code in any project repo. '
        'Lists every GritQL rule, what triggers it, the documenting guide, '
        'and how to verify.'
    )


# Back-compat: callers that imported the module-level constant continue to
# work; the value is the current (single-language or first-language)
# description at import time. Prefer `rules_index_description()` for code
# that should reflect live config changes.
RULES_INDEX_DESCRIPTION = rules_index_description()
PYTHON_COMPLEXITY_SKILL_ID = 'python-complexity'
PYTHON_COMPLEXITY_TITLE = 'Python Complexity (radon)'
PYTHON_COMPLEXITY_DESCRIPTION = (
    'Use when writing or editing Python. regin enforces a cyclomatic-complexity '
    'threshold on every Python edit; this skill documents the configured grade '
    'and how to verify locally.'
)


def deploy_rules_index_skill(rules_md_path: str) -> str:
    """Deploy the master rule index skill (`grit-rules`).

    The body is the pre-rendered `.grit/RULES.md` plus a short preamble so
    agents know how to run checks. Returns the skill path.
    """
    _validate_id(RULES_INDEX_SKILL_ID)

    if not os.path.exists(rules_md_path):
        raise FileNotFoundError(
            f"Rules markdown not found at {rules_md_path}. "
            "Run `regin rules index` first."
        )

    with open(rules_md_path, 'r') as f:
        rules_body = f.read()

    from lib.rule_engines import get as _get_engine
    engine = _get_engine('grit')
    language_hint = ', '.join(engine.language_ids)
    preamble = (
        "This skill is the single source of truth for every rule shipped "
        f"by the `{engine.id}` rule engine (languages: {language_hint}). "
        "Consult it before writing or editing code in any project repo, "
        "then run the relevant `grit apply` commands before claiming work "
        "is done.\n"
    )

    skill_path = get_skill_path(RULES_INDEX_SKILL_ID)
    skill_dir = os.path.dirname(skill_path)
    os.makedirs(skill_dir, exist_ok=True)

    # Write the full rule index inline in SKILL.md — the Skill tool loads it
    # directly on invocation (no content.md shim).
    skill_content = (
        f"---\n"
        f"name: {RULES_INDEX_SKILL_ID}\n"
        f"description: \"{rules_index_description()}\"\n"
        f"---\n\n"
        f"{preamble}\n{rules_body}\n"
    )
    with open(skill_path, 'w') as f:
        f.write(skill_content)

    # This deploy path doesn't rebuild the skill dir from scratch, so drop a
    # stale content.md left by an older shim-style deploy.
    legacy_content_md = os.path.join(skill_dir, 'content.md')
    if os.path.isfile(legacy_content_md):
        os.remove(legacy_content_md)

    # Runner scripts ship from the regin repo's `scripts/`; the .grit sources
    # ship from the engine's configured grit_dir (user-local). check_grit.sh
    # shells out to filter_grit_output.py, so all three must ship together.
    # Rebuild the scripts dir so renamed/removed scripts (e.g. the old
    # check_patterns.sh) don't linger across redeploys.
    scripts_src = os.path.join(str(settings.project_root), 'scripts')
    grit_src = engine.grit_dir

    scripts_dir = os.path.join(skill_dir, 'scripts')
    if os.path.isdir(scripts_dir):
        shutil.rmtree(scripts_dir)
    os.makedirs(scripts_dir, exist_ok=True)
    for name in ('check_grit.sh', 'filter_grit_output.py', 'find_applicable_files.py'):
        src = os.path.join(scripts_src, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(scripts_dir, name))

    grit_dest = os.path.join(skill_dir, '.grit')
    if os.path.isdir(grit_src):
        if os.path.isdir(grit_dest):
            shutil.rmtree(grit_dest)
        shutil.copytree(grit_src, grit_dest)

    return skill_path


def deploy_python_complexity_skill() -> str:
    """Deploy the `python-complexity` auto-skill via the pattern path.

    Writes the templated body to `<patterns_dir>/python-complexity/SKILL.md`
    (the source-of-truth), registers a pattern_doc row on first run, then
    delegates to `deploy_pattern_as_skill` for the actual provider deploy.

    Going through the pattern path (not a bespoke templated shim) means:
      - The rules page guide link routes to /patterns/python-complexity.
      - The patterns rules-toggle UI can disable
        `python.cyclomatic-complexity.*` rules per engine.
      - `regenerate` overwrites the body the same way `grit-rules` does.
    """
    _validate_id(PYTHON_COMPLEXITY_SKILL_ID)

    from lib.rule_engines import get as _get_engine, all_engines
    engine = None
    for eng in all_engines():
        if getattr(eng, 'kind', '') == 'radon':
            engine = eng
            break
    if engine is None:
        try:
            engine = _get_engine('radon')
        except KeyError:
            raise RuntimeError(
                'No radon rule engine is configured; cannot deploy python-complexity.'
            )

    languages = ', '.join(engine.language_ids)
    body = (
        f"This skill is the user-facing documentation for the `{engine.id}` "
        f"rule engine (languages: {languages}).\n\n"
        f"## What regin enforces\n\n"
        f"Every Python edit (Edit / Write / MultiEdit) triggers radon's "
        f"cyclomatic-complexity (CC) check. Any function whose grade is "
        f"`{engine.min_grade}` or worse is reported at severity "
        f"`{engine.severity}`.\n\n"
        f"Grade scale: A (CC 1-5) -> B (6-10) -> C (11-20) -> D (21-30) -> "
        f"E (31-40) -> F (41+).\n\n"
        f"## What to do when flagged\n\n"
        f"- Split the function: extract helpers for distinct branches or loops.\n"
        f"- Replace nested conditionals with early returns / guard clauses.\n"
        f"- Move tabular logic into a lookup dict.\n"
        f"- If the complexity is intrinsic and unavoidable, leave a one-line "
        f"comment explaining why and accept the warning.\n\n"
        f"## Verify locally\n\n"
        f"```\n"
        f"radon cc -s --min {engine.min_grade} <file.py>\n"
        f"```\n\n"
        f"Use `radon cc -s -a <dir>` for a directory-wide summary.\n"
    )
    title = PYTHON_COMPLEXITY_TITLE
    pattern_source_skill_md = (
        "---\n"
        f"title: \"{title}\"\n"
        f"description: \"{PYTHON_COMPLEXITY_DESCRIPTION}\"\n"
        f"procedure: {PYTHON_COMPLEXITY_SKILL_ID}\n"
        "source_repos: [auto-generated]\n"
        "exemplar_count: 0\n"
        "manual: false\n"
        "auto_skill_engine: radon\n"
        "---\n\n"
        f"{body}"
    )

    pattern_source_dir = os.path.join(str(settings.patterns_dir), PYTHON_COMPLEXITY_SKILL_ID)
    os.makedirs(pattern_source_dir, exist_ok=True)
    source_skill_md = os.path.join(pattern_source_dir, 'SKILL.md')
    with open(source_skill_md, 'w') as f:
        f.write(pattern_source_skill_md)

    # Register the pattern_doc row on first run; subsequent regenerates
    # just refresh the SKILL.md content (which deploy_pattern_as_skill picks up).
    from lib.orm import SessionLocal
    from lib.orm.models import PatternDoc
    from sqlmodel import select
    import hashlib
    with SessionLocal() as session:
        existing = session.exec(
            select(PatternDoc).where(PatternDoc.slug == PYTHON_COMPLEXITY_SKILL_ID)
        ).first()
        with open(source_skill_md, 'rb') as f:
            c_hash = hashlib.sha256(f.read()).hexdigest()
        if existing is None:
            doc = PatternDoc(
                slug=PYTHON_COMPLEXITY_SKILL_ID,
                title=title,
                file_path=os.path.relpath(source_skill_md, str(settings.project_root)),
                category='procedure',
                content_hash=c_hash,
                source_kind='pattern',
            )
            session.add(doc)
        else:
            existing.content_hash = c_hash
        session.commit()

    return deploy_pattern_as_skill(
        pattern_source_dir, PYTHON_COMPLEXITY_SKILL_ID, title,
    )


def _parse_simple_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith('---'):
        return {}
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v is not None}


def undeploy_skill(procedure_id, target_dir=None):
    """Remove the skill directory. Returns True if removed."""
    _validate_id(procedure_id)
    skill_dir = os.path.join(_resolve_base(target_dir), procedure_id)
    if os.path.isdir(skill_dir):
        shutil.rmtree(skill_dir)
        return True
    return False
