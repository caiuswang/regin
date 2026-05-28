"""Orchestrate GritQL rule discovery → rules.json + RULES.md + guide patches.

This module is the single source of truth for what GritQL rules the
repo ships. Pure parsing lives in `lib.utils.grit_parser`; the
responsibilities kept here are I/O-heavy: writing the JSON index, the
markdown index, stripping outdated `## Verification` blocks from
pattern guides, and maintaining the `rules_disabled.txt` disable list.

The parse functions below (`parse_grit_rules`, `missing_metadata`,
`_iter_grit_files`, `_parse_file`) are thin no-arg wrappers that bind
the grit-patterns directory and project root from `lib.settings`. They
are retained as a public surface so existing blueprints, scripts, and
tests don't need to thread those paths through every call.
"""

import json
import os
import re
import shutil

from lib.settings import settings
from lib.utils import grit_parser as _grit_parser
from lib.utils.grit_parser import RuleMetadataError  # re-export
from lib.activity_log import get_activity_logger as _get_activity_logger


def _rules_log():
    return _get_activity_logger("rules")

# The .grit/patterns/<language>/ layout is owned by lib.rule_engines.grit.
# `GRIT_LANGUAGE_IDS` mirrors the engine's configured `language_ids`; the
# per-language dirs are derived from it. `GRIT_PATTERNS_DIR` and
# `GRIT_PATTERNS_DIRS` are kept module-level so tests and scripts can
# monkeypatch them.
def _configured_grit_language_ids() -> tuple[str, ...]:
    from lib.rule_engines import get as _get_engine
    try:
        return tuple(_get_engine('grit').language_ids)
    except KeyError:
        return ('java',)


def _default_patterns_dirs(language_ids: tuple[str, ...]) -> list[str]:
    from lib.rule_engines import get as _get_engine
    try:
        engine = _get_engine('grit')
        return [engine.patterns_dir(lang) for lang in language_ids]
    except KeyError:
        return [os.path.join(str(settings.grit_dir), 'patterns', lang) for lang in language_ids]


GRIT_LANGUAGE_IDS = _configured_grit_language_ids()
GRIT_PATTERNS_DIRS = _default_patterns_dirs(GRIT_LANGUAGE_IDS)
GRIT_PATTERNS_DIR = GRIT_PATTERNS_DIRS[0] if GRIT_PATTERNS_DIRS else os.path.join(str(settings.grit_dir), 'patterns', 'java')
RULES_JSON_PATH = os.path.join(str(settings.grit_dir), 'rules.json')
RULES_MD_PATH = os.path.join(str(settings.grit_dir), 'RULES.md')
DISABLED_RULES_PATH = os.path.join(str(settings.grit_dir), 'rules_disabled.txt')

# Re-export so callers that reach through this module for constants /
# regexes (several tests, `scripts/filter_grit_output.py`) keep working.
REQUIRED_FIELDS = _grit_parser.REQUIRED_FIELDS
_RULE_LINE_RE = _grit_parser._RULE_LINE_RE
_PATTERN_DECL_RE = _grit_parser._PATTERN_DECL_RE


def refresh_language_dirs() -> None:
    """Recompute the module-level grit-patterns dir constants from current
    settings.

    The constants above are bound once at import time. Call this after the
    grit engine's configured languages change at runtime (e.g. a grit-rule
    import enabling a new language via `rule_engines.ensure_grit_languages`)
    so `regenerate()` and the index helpers pick up the new per-language
    dirs without a process restart.
    """
    global GRIT_LANGUAGE_IDS, GRIT_PATTERNS_DIRS, GRIT_PATTERNS_DIR
    GRIT_LANGUAGE_IDS = _configured_grit_language_ids()
    GRIT_PATTERNS_DIRS = _default_patterns_dirs(GRIT_LANGUAGE_IDS)
    GRIT_PATTERNS_DIR = (
        GRIT_PATTERNS_DIRS[0] if GRIT_PATTERNS_DIRS
        else os.path.join(str(settings.grit_dir), 'patterns', 'java')
    )


_GUIDE_LINE_RE = re.compile(r'^(\s*//\s*@rule\s+guide\s*=).*$', re.MULTILINE)


def _copy_grit_file(src_path: str, dest_path: str, guide: str | None) -> None:
    """Copy one `.grit` source. When `guide` is set, rewrite every
    `// @rule guide=…` line to that guide so a bundle's rules attach to the
    pattern they were imported as (critical when the import renames the slug;
    otherwise the rules index under a guide with no deployable skill and stay
    disabled forever)."""
    if not guide:
        shutil.copy2(src_path, dest_path)
        return
    with open(src_path, 'r', encoding='utf-8') as fh:
        content = fh.read()
    content = _GUIDE_LINE_RE.sub(lambda m: m.group(1) + guide, content)
    with open(dest_path, 'w', encoding='utf-8') as fh:
        fh.write(content)


def install_grit_sources(src_grit_dir: str, *, guide: str | None = None) -> dict:
    """Copy a bundle's `.grit/patterns/<lang>/*.grit` sources into the active
    grit_dir so the rule engine and index pick them up.

    `src_grit_dir` is the bundle's `.grit/` directory. Only `<lang>` dirs
    whose id is a known language (`lib.languages`) are installed; unknown
    dirs are reported under `skipped` and left alone. Same-named files in the
    destination are overwritten — the bundle owns its rule file. When `guide`
    is given, each rule's `@rule guide=` line is rewritten to it so the rules
    bind to the importing pattern's slug.

    Returns {'languages': [...], 'files': [...rel-to-grit_dir...], 'skipped': [...]}.
    """
    patterns_root = os.path.join(src_grit_dir, 'patterns')
    if not os.path.isdir(patterns_root):
        return {'languages': [], 'files': [], 'skipped': []}

    from lib import languages as _langs
    try:
        from lib.rule_engines import get as _get_engine
        engine = _get_engine('grit')
        dest_for = engine.patterns_dir
    except KeyError:
        def dest_for(lang: str) -> str:
            return os.path.join(str(settings.grit_dir), 'patterns', lang)

    languages_done: list[str] = []
    files_done: list[str] = []
    skipped: list[str] = []
    for lang in sorted(os.listdir(patterns_root)):
        lang_src = os.path.join(patterns_root, lang)
        if not os.path.isdir(lang_src):
            continue
        try:
            _langs.get(lang)
        except KeyError:
            skipped.append(lang)
            continue
        grit_files = [f for f in sorted(os.listdir(lang_src)) if f.endswith('.grit')]
        if not grit_files:
            continue
        dest_dir = dest_for(lang)
        os.makedirs(dest_dir, exist_ok=True)
        for fname in grit_files:
            _copy_grit_file(os.path.join(lang_src, fname),
                            os.path.join(dest_dir, fname), guide)
            files_done.append(os.path.join('patterns', lang, fname))
        languages_done.append(lang)

    _rules_log().write(
        'grit_sources_installed',
        languages=languages_done, file_count=len(files_done), skipped=skipped,
    )
    return {'languages': languages_done, 'files': files_done, 'skipped': skipped}


def _load_repo_rules(path: str) -> list[dict]:
    """Read the `rules` list from a repo-local `.grit/rules.json` (or [])."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            return json.load(f).get('rules', [])
    except (OSError, json.JSONDecodeError):
        return []


def _guide_source_files(rules: list[dict]) -> dict[str, str]:
    """Map {rel-under-grit_dir: abs-path} for the `.grit` sources backing
    `rules`. `source_file` is recorded relative to `settings.project_root`;
    we resolve it to an abs path, then re-express it relative to the global
    `grit_dir` so it copies into a repo `.grit/` preserving `patterns/<lang>/`."""
    out: dict[str, str] = {}
    for r in rules:
        src_rel = r.get('source_file')
        if not src_rel:
            continue
        abs_src = os.path.normpath(os.path.join(str(settings.project_root), src_rel))
        if not os.path.isfile(abs_src):
            continue
        out[os.path.relpath(abs_src, str(settings.grit_dir))] = abs_src
    return out


def sync_guide_rules_to_repo(slug: str, repo_root: str) -> dict:
    """Install a guide's grit rules into `<repo_root>/.grit/` so the repo-local
    PostToolUse hook enforces them.

    The hook resolves rules from the `.grit/` walked up from the edited file
    (not the global grit_dir), so a project-deployed pattern only enforces
    once its rules live in the target repo's `.grit/`. Copies the guide's
    `.grit/patterns/<lang>/*.grit` sources and merges its rule entries into
    `<repo_root>/.grit/rules.json` (replacing this guide's prior entries,
    leaving other guides' entries intact). No-op if the guide has no rules.

    Returns {'rules': int, 'files': int, 'languages': [...]}.
    """
    rules = rules_for_guide(slug)
    if not rules:
        return {'rules': 0, 'files': 0, 'languages': []}
    repo_grit = os.path.join(repo_root, '.grit')
    sources = _guide_source_files(rules)
    langs: set[str] = set()
    for rel, abs_src in sources.items():
        dest = os.path.join(repo_grit, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(abs_src, dest)
        parts = rel.split(os.sep)
        if len(parts) >= 2 and parts[0] == 'patterns':
            langs.add(parts[1])
    repo_rules_json = os.path.join(repo_grit, 'rules.json')
    merged = [r for r in _load_repo_rules(repo_rules_json) if r.get('guide') != slug]
    merged.extend(rules)
    write_rules_json(merged, path=repo_rules_json)
    _rules_log().write(
        'grit_rules_synced_to_repo', slug=slug, repo=repo_root,
        rules=len(rules), files=len(sources), languages=sorted(langs),
    )
    return {'rules': len(rules), 'files': len(sources), 'languages': sorted(langs)}


def _prune_empty_parents(start_dir: str, stop: str) -> None:
    """Remove `start_dir` and each empty parent, walking up until (but not
    including) `stop`. Leaves `stop` (the repo `.grit/`) and its siblings
    like `.gritmodules` untouched."""
    cur = os.path.abspath(start_dir)
    stop = os.path.abspath(stop)
    while cur != stop and cur.startswith(stop + os.sep):
        try:
            os.rmdir(cur)  # raises OSError unless empty
        except OSError:
            break
        cur = os.path.dirname(cur)


def _prune_unreferenced_sources(repo_grit: str, candidate_rels, kept_rels) -> None:
    """Delete each `candidate_rel` source file under `repo_grit` that no
    longer appears in `kept_rels`, then prune any directories it emptied."""
    for rel in candidate_rels:
        if rel in kept_rels:
            continue
        f = os.path.join(repo_grit, rel)
        if os.path.isfile(f):
            os.remove(f)
            _prune_empty_parents(os.path.dirname(f), repo_grit)


def remove_guide_rules_from_repo(slug: str, repo_root: str) -> dict:
    """Inverse of `sync_guide_rules_to_repo`: drop a guide's entries from
    `<repo_root>/.grit/rules.json` and delete any source `.grit` file no
    remaining rule references. Removes an emptied rules.json. Best-effort."""
    repo_grit = os.path.join(repo_root, '.grit')
    repo_rules_json = os.path.join(repo_grit, 'rules.json')
    existing = _load_repo_rules(repo_rules_json)
    removed = [r for r in existing if r.get('guide') == slug]
    if not removed:
        return {'removed': 0}
    remaining = [r for r in existing if r.get('guide') != slug]
    _prune_unreferenced_sources(
        repo_grit, _guide_source_files(removed), set(_guide_source_files(remaining)),
    )
    if remaining:
        write_rules_json(remaining, path=repo_rules_json)
    elif os.path.isfile(repo_rules_json):
        os.remove(repo_rules_json)
    _rules_log().write(
        'grit_rules_removed_from_repo', slug=slug, repo=repo_root,
        removed=len(removed),
    )
    return {'removed': len(removed)}


def remove_guide_rules(slug: str) -> dict:
    """Inverse of a bundle import's grit-merge: drop every rule a guide
    installed into the active `grit_dir` and regenerate the index.

    `_merge_grit_rules` (pattern import) copies a bundle's `.grit` sources
    into `grit_dir` rewriting each `@rule guide=` line to the importing slug,
    so a guide's rules are exactly those with `guide == slug` in the index.
    This deletes each of those rule blocks from their `.grit` source files
    (removing a file left empty), then regenerates `rules.json` / `RULES.md`.
    Language configuration is left alone — other bundles may use it.

    Callers redeploy the `grit-rules` skill afterwards. No-op when the guide
    installed no rules. Returns {'removed': int, 'rule_ids': [...]}.
    """
    rule_ids = [r['id'] for r in rules_for_guide(slug)]
    if not rule_ids:
        return {'removed': 0, 'rule_ids': []}
    removed: list[str] = []
    for rid in rule_ids:
        if delete_rule(rid):
            removed.append(rid)
    regenerate(write_guides=False)
    _rules_log().write(
        'grit_guide_rules_removed', slug=slug, removed=removed,
    )
    return {'removed': len(removed), 'rule_ids': removed}


def _active_patterns_dirs() -> list[str]:
    """Return the per-language pattern dirs to walk. When a test or caller
    has monkeypatched `GRIT_PATTERNS_DIR` away from the default, honour that
    single dir (back-compat); otherwise walk every configured language dir.
    """
    if GRIT_PATTERNS_DIRS and GRIT_PATTERNS_DIR == GRIT_PATTERNS_DIRS[0]:
        return list(GRIT_PATTERNS_DIRS)
    return [GRIT_PATTERNS_DIR]


def parse_grit_rules() -> list[dict]:
    """Walk every configured `.grit/patterns/<lang>/*.grit` dir, return a
    combined list of rule dicts.

    Each rule dict has keys: id, layer, triggers (list[str]), severity, guide,
    summary, source_file (relative to project root).

    Patterns without a complete `@rule` header block are skipped silently —
    they are typically helper patterns composed by top-level `file(...)`
    matchers. Use `missing_metadata()` to list them for CI.
    """
    rules: list[dict] = []
    for d in _active_patterns_dirs():
        rules.extend(_grit_parser.parse_grit_rules(d, str(settings.project_root)))
    return rules


def missing_metadata() -> list[tuple[str, str, list[str]]]:
    """Return [(relative_path, pattern_name, missing_fields)] for every
    `pattern foo()` declaration missing one or more required @rule fields,
    across every configured language dir.
    """
    out: list[tuple[str, str, list[str]]] = []
    for d in _active_patterns_dirs():
        out.extend(_grit_parser.missing_metadata(d, str(settings.project_root)))
    return out


# ---------------------------------------------------------------------------
# Rule disable list
# ---------------------------------------------------------------------------

def load_disabled_rule_ids() -> set[str]:
    """Read `.grit/rules_disabled.txt`. Returns the set of disabled rule ids.

    The file is plain-text, one rule id per line, `#` starts a comment, and
    blank lines are ignored.
    """
    if not os.path.exists(DISABLED_RULES_PATH):
        return set()
    ids: set[str] = set()
    with open(DISABLED_RULES_PATH, 'r') as f:
        for raw in f:
            line = raw.split('#', 1)[0].strip()
            if line:
                ids.add(line)
    return ids


def _write_disabled_rule_ids(ids: set[str]) -> None:
    os.makedirs(os.path.dirname(DISABLED_RULES_PATH), exist_ok=True)
    sorted_ids = sorted(ids)
    lines = [
        '# Rules listed here are skipped by the PostToolUse hook and by',
        '# scripts/check_grit.sh. The grit source is untouched — re-enable',
        '# by removing the id (or run `regin rules enable --id X`).',
    ]
    lines.extend(sorted_ids)
    with open(DISABLED_RULES_PATH, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def set_rules_disabled(rule_ids, disabled: bool) -> set[str]:
    """Add or remove a batch of rule ids from the disable list.

    Returns the new disabled set (post-change).
    """
    current = load_disabled_rule_ids()
    if disabled:
        current.update(rule_ids)
    else:
        current.difference_update(rule_ids)
    _write_disabled_rule_ids(current)
    _rules_log().write(
        "grit_rules_toggled",
        rule_ids=sorted(rule_ids), disabled=disabled,
        total_disabled=len(current),
    )
    return current


def write_rules_json(rules: list[dict], path: str = RULES_JSON_PATH) -> None:
    by_layer: dict[str, list[str]] = {}
    by_trigger: dict[str, list[str]] = {}
    by_guide: dict[str, list[str]] = {}
    for r in rules:
        by_layer.setdefault(r['layer'], []).append(r['id'])
        by_guide.setdefault(r['guide'], []).append(r['id'])
        for trig in r['triggers']:
            by_trigger.setdefault(trig, []).append(r['id'])
    payload = {
        'version': 1,
        'rules': rules,
        'by_layer': {k: sorted(v) for k, v in sorted(by_layer.items())},
        'by_trigger': {k: sorted(v) for k, v in sorted(by_trigger.items())},
        'by_guide': {k: sorted(v) for k, v in sorted(by_guide.items())},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2, sort_keys=False)
        f.write('\n')


def _language_extensions(language_ids: tuple[str, ...]) -> list[str]:
    """Resolve file extensions for the configured languages. Unknown ids
    are silently skipped — the registry decides what's a known language."""
    from lib import languages as _langs
    exts: list[str] = []
    for lang_id in language_ids:
        try:
            for ext in _langs.get(lang_id).file_extensions:
                if ext not in exts:
                    exts.append(ext)
        except KeyError:
            continue
    return exts


def write_rules_md(rules: list[dict], path: str = RULES_MD_PATH) -> None:
    """Write a compact, skill-friendly markdown rule index grouped by layer."""
    langs = GRIT_LANGUAGE_IDS or ('java',)
    langs_glob = '{' + ','.join(langs) + '}' if len(langs) > 1 else langs[0]
    exts = _language_extensions(langs) or ['.java']
    exts_pretty = ' / '.join(f'`{e}`' for e in exts)

    lines: list[str] = []
    lines.append('# GritQL rule index')
    lines.append('')
    lines.append(f'Auto-generated from `.grit/patterns/{langs_glob}/*.grit`. Do not hand-edit.')
    lines.append('')
    lines.append(f'Total rules: **{len(rules)}**')
    lines.append('')
    lines.append(
        '> **Note:** A PostToolUse hook already runs the applicable rules '
        f'against every {exts_pretty} file you Edit/Write and surfaces violations '
        'automatically. You do **not** need to re-run `grit apply` on a '
        'file you just edited. Use the commands below for **bulk sweeps** '
        '(entire repo), **CI/pre-commit**, or **auditing code you did not '
        'touch this session**.'
    )
    lines.append('')

    by_layer: dict[str, list[dict]] = {}
    for r in rules:
        by_layer.setdefault(r['layer'], []).append(r)

    for layer in sorted(by_layer.keys()):
        layer_rules = sorted(by_layer[layer], key=lambda r: r['id'])
        lines.append(f'## Layer: `{layer}`')
        lines.append('')
        lines.append('| Rule ID | Triggers | Severity | Guide | Summary |')
        lines.append('|---|---|---|---|---|')
        for r in layer_rules:
            trig = ', '.join(f'`{t}`' for t in r['triggers'])
            lines.append(
                f"| `{r['id']}` | {trig} | {r['severity']} | "
                f"`{r['guide']}` | {r['summary']} |"
            )
        lines.append('')

    lines.append('## Running checks')
    lines.append('')
    lines.append('This skill deploys with `scripts/check_grit.sh` and a `.grit` directory. '
                 'Use the script to run checks against any repo:')
    lines.append('')
    lines.append('```bash')
    lines.append('# All rules against a repo')
    lines.append('~/.claude/skills/grit-rules/scripts/check_grit.sh <repo-path>')
    lines.append('')
    lines.append('# A single rule')
    lines.append('~/.claude/skills/grit-rules/scripts/check_grit.sh <repo-path> <rule-id>')
    lines.append('```')
    lines.append('')

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


def load_rules_index() -> dict:
    """Load `.grit/rules.json` into memory. Returns `{'rules': [], ...}` if
    the index has not been generated yet."""
    if not os.path.exists(RULES_JSON_PATH):
        return {'version': 1, 'rules': [], 'by_layer': {}, 'by_trigger': {}, 'by_guide': {}}
    with open(RULES_JSON_PATH, 'r') as f:
        return json.load(f)


def rules_for_guide(procedure_id: str) -> list[dict]:
    """Return all rules whose `guide` field matches the given procedure id."""
    data = load_rules_index()
    ids = set(data.get('by_guide', {}).get(procedure_id, []))
    if not ids:
        return []
    return [r for r in data['rules'] if r['id'] in ids]


def delete_rule(rule_id: str) -> bool:
    """Delete a rule from its .grit source file. Returns True if deleted."""
    data = load_rules_index()
    rule = next((r for r in data.get('rules', []) if r['id'] == rule_id), None)
    if not rule:
        return False

    source_path = os.path.join(str(settings.project_root), rule['source_file'])
    if not os.path.isfile(source_path):
        return False

    with open(source_path, 'r') as f:
        content = f.read()

    # Find the rule block: @rule comments + pattern declaration + body
    # Look for the pattern declaration
    marker = f"pattern {rule_id}("
    idx = content.find(marker)
    if idx == -1:
        return False

    # Find start: walk back to find the first @rule comment
    start = content.rfind('\n\n', 0, idx)
    start = 0 if start == -1 else start

    # Find end: match braces to find end of pattern body
    depth = 0
    end = idx
    in_pattern = False
    for i in range(idx, len(content)):
        ch = content[i]
        if ch == '{':
            depth += 1
            in_pattern = True
        elif ch == '}':
            depth -= 1
            if in_pattern and depth == 0:
                end = i + 1
                break

    # Remove the block (and any trailing blank lines)
    after = content[end:]
    while after.startswith('\n'):
        after = after[1:]
    new_content = content[:start]
    if new_content and not new_content.endswith('\n'):
        new_content += '\n'
    if after:
        new_content += '\n' + after

    # If file would be empty (or just whitespace), remove it
    if not new_content.strip():
        os.remove(source_path)
    else:
        with open(source_path, 'w') as f:
            f.write(new_content)

    # Remove from disabled list if present
    disabled = load_disabled_rule_ids()
    if rule_id in disabled:
        disabled.discard(rule_id)
        _write_disabled_rule_ids(disabled)

    _rules_log().write(
        "grit_rule_deleted",
        rule_id=rule_id, source_file=rule['source_file'],
        file_removed=not os.path.isfile(source_path),
    )
    return True


def update_rule(rule_id: str, updates: dict) -> bool:
    """Update a rule's @rule metadata and/or GritQL source in its .grit file.

    `updates` can contain: summary, severity, triggers (comma-separated str),
    layer, guide, and source (the full GritQL pattern body).
    Returns True if updated.
    """
    data = load_rules_index()
    rule = next((r for r in data.get('rules', []) if r['id'] == rule_id), None)
    if not rule:
        return False

    source_path = os.path.join(str(settings.project_root), rule['source_file'])
    if not os.path.isfile(source_path):
        return False

    with open(source_path, 'r') as f:
        content = f.read()

    marker = f"pattern {rule_id}("
    idx = content.find(marker)
    if idx == -1:
        return False

    # Find the full block (comments + pattern body)
    start = content.rfind('\n\n', 0, idx)
    start = 0 if start == -1 else start + 2

    depth = 0
    end = idx
    in_pattern = False
    for i in range(idx, len(content)):
        ch = content[i]
        if ch == '{':
            depth += 1
            in_pattern = True
        elif ch == '}':
            depth -= 1
            if in_pattern and depth == 0:
                end = i + 1
                break

    old_block = content[start:end]

    if 'source' in updates:
        # Replace the entire block with user-provided source
        new_block = updates['source'].rstrip()
    else:
        # Update only @rule metadata lines, keep GritQL body
        lines = old_block.split('\n')
        new_lines = []
        meta_fields = {k: v for k, v in updates.items() if k in ('summary', 'severity', 'triggers', 'layer', 'guide')}
        for line in lines:
            m = _RULE_LINE_RE.match(line)
            if m and m.group(1) in meta_fields:
                new_lines.append(f'// @rule {m.group(1)}={meta_fields[m.group(1)]}')
            else:
                new_lines.append(line)
        new_block = '\n'.join(new_lines)

    new_content = content[:start] + new_block + content[end:]
    with open(source_path, 'w') as f:
        f.write(new_content)

    _rules_log().write(
        "grit_rule_updated",
        rule_id=rule_id, source_file=rule['source_file'],
        updated_fields=sorted(updates.keys()),
    )
    return True


def _undeployed_guides() -> set[str]:
    """Return guide (procedure) ids whose skill is not deployed.

    Rules linked to an undeployed guide are auto-disabled so the hook
    doesn't enforce patterns the agent has no skill for.
    """
    from lib.skills import skill_registry
    from lib.skills.skill_registry import deployed_exists
    undeployed: set[str] = set()
    for skill_id in skill_registry.all_ids():
        entry = skill_registry.get(skill_id)
        if entry.get('type') == 'pattern' and not deployed_exists(skill_id):
            undeployed.add(entry['procedure_id'])
    return undeployed


def regenerate(write_guides: bool = True) -> dict:
    """Parse grit files, write rules.json + RULES.md, and (optionally)
    strip any leftover `## Verification` sections from pattern guides.
    Verification is surfaced via the web UI `/rules` view and the
    `grit-rules` skill — not duplicated into per-guide markdown.

    Rules whose guide's skill is not deployed are auto-disabled.

    Returns a summary dict with counts.
    """
    rules = parse_grit_rules()
    disabled = load_disabled_rule_ids()
    undeployed = _undeployed_guides()
    for r in rules:
        r['disabled'] = r['id'] in disabled or r.get('guide', '') in undeployed
    write_rules_json(rules)
    write_rules_md(rules)

    guides_updated = 0
    if write_guides:
        guides_updated = _strip_verification_sections()

    _rules_log().write(
        "grit_rules_regenerated",
        rule_count=len(rules), guides_updated=guides_updated,
        write_guides=write_guides,
    )
    return {
        'rules': len(rules),
        'guides_updated': guides_updated,
        'rules_json': RULES_JSON_PATH,
        'rules_md': RULES_MD_PATH,
    }


# ---------------------------------------------------------------------------
# Internals — thin wrappers around `lib.utils.grit_parser` that bind the
# module-level directory constants. Kept as `_`-prefixed names so existing
# tests that patch them through this module keep working.
# ---------------------------------------------------------------------------

def _iter_grit_files():
    for d in _active_patterns_dirs():
        yield from _grit_parser.iter_grit_files(d)


def _parse_file(path: str) -> list[dict]:
    return _grit_parser.parse_file(path, str(settings.project_root))


_VERIFY_SECTION_RE = re.compile(
    r'\n## Verification\n.*?(?=\n## |\Z)',
    re.DOTALL,
)


def _strip_verification_sections() -> int:
    """Remove any `## Verification` section from every pattern guide.

    The Rules view in the web UI and the `grit-rules` skill are now
    the authoritative surfaces for "what enforces this procedure", so the
    per-guide markdown should not duplicate that information.
    """
    patterns_dir = str(settings.patterns_dir)
    if not os.path.isdir(patterns_dir):
        return 0

    updated = 0
    for name in sorted(os.listdir(patterns_dir)):
        if name.startswith('_') or name.startswith('.'):
            continue
        skill_md = os.path.join(patterns_dir, name, 'SKILL.md')
        if not os.path.isfile(skill_md):
            continue
        if _strip_verification_section(skill_md):
            updated += 1
    return updated


def _strip_verification_section(path: str) -> bool:
    with open(path, 'r') as f:
        content = f.read()

    if not _VERIFY_SECTION_RE.search(content):
        return False

    new_content = _VERIFY_SECTION_RE.sub('', content, count=1)
    # Ensure the file ends with a single trailing newline
    new_content = new_content.rstrip() + '\n'

    if new_content == content:
        return False

    with open(path, 'w') as f:
        f.write(new_content)
    return True
