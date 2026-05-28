"""Pure parsing for GritQL `.grit` files and their `// @rule` metadata.

Extracted from `lib.rules.grit_rule_index` so the parse step can be used by
anything that needs to inspect rule metadata (a linter, a CI check, a
one-off audit) without pulling in the orchestrator's filesystem-write
and skill-deployment dependencies. Each function takes the directories
it needs as arguments — no module-level globals, no side effects.

The orchestrator in `lib.rules.grit_rule_index` re-exports these symbols
and provides no-arg bound versions that fill in `GRIT_PATTERNS_DIR`
and `PROJECT_ROOT` from `lib.settings`.
"""

from __future__ import annotations

import os
import re
from typing import Iterator


REQUIRED_FIELDS = ('id', 'layer', 'triggers', 'severity', 'guide', 'summary')

_RULE_LINE_RE = re.compile(r'^\s*//\s*@rule\s+([a-z_]+)\s*=\s*(.*?)\s*$')
_PATTERN_DECL_RE = re.compile(r'^\s*pattern\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(')


class RuleMetadataError(ValueError):
    """Raised when a @rule block declares an id that doesn't match the
    following `pattern <name>(` declaration. Signals a typo or copy/paste
    error in the .grit source."""


def iter_grit_files(grit_patterns_dir: str) -> Iterator[str]:
    """Yield absolute paths to every `*.grit` file under the given directory.

    Returns nothing (not an error) when the directory is missing — the
    caller decides whether that is expected.
    """
    if not os.path.isdir(grit_patterns_dir):
        return
    for name in os.listdir(grit_patterns_dir):
        if name.endswith('.grit'):
            yield os.path.join(grit_patterns_dir, name)


def parse_file(path: str, project_root: str) -> list[dict]:
    """Parse one `.grit` file into a list of rule dicts.

    Each rule dict has keys: id, layer, triggers (list[str]), severity,
    guide, summary, source_file (relative to `project_root`). Patterns
    without a complete `@rule` header block are silently skipped.

    Raises `RuleMetadataError` if a `@rule id=X` block precedes a
    `pattern Y(` declaration where X != Y.
    """
    with open(path, 'r') as f:
        lines = f.readlines()

    rules: list[dict] = []
    pending: dict = {}
    rel = os.path.relpath(path, project_root)

    for line in lines:
        m_line = _RULE_LINE_RE.match(line)
        if m_line:
            pending[m_line.group(1)] = m_line.group(2)
            continue

        m_decl = _PATTERN_DECL_RE.match(line)
        if m_decl:
            pattern_name = m_decl.group(1)
            if all(f in pending for f in REQUIRED_FIELDS):
                if pending['id'] != pattern_name:
                    raise RuleMetadataError(
                        f"{rel}: @rule id='{pending['id']}' does not match "
                        f"pattern name '{pattern_name}'"
                    )
                rules.append({
                    'id': pending['id'],
                    'layer': pending['layer'],
                    'triggers': [t.strip() for t in pending['triggers'].split(',') if t.strip()],
                    'severity': pending['severity'],
                    'guide': pending['guide'],
                    'summary': pending['summary'],
                    'source_file': rel,
                })
            pending = {}
            continue

        # Any non-blank, non-comment line between @rule tags and the
        # pattern declaration resets the pending block — helper code in
        # between should not carry metadata over.
        if line.strip() and not line.lstrip().startswith('//'):
            pending = {}

    return rules


def parse_grit_rules(grit_patterns_dir: str, project_root: str) -> list[dict]:
    """Aggregate rule dicts from every `.grit` file under the directory."""
    rules: list[dict] = []
    for path in sorted(iter_grit_files(grit_patterns_dir)):
        rules.extend(parse_file(path, project_root))
    return rules


def missing_metadata(
    grit_patterns_dir: str,
    project_root: str,
) -> list[tuple[str, str, list[str]]]:
    """Return `[(relative_path, pattern_name, missing_fields)]` for every
    `pattern foo()` declaration missing one or more required @rule fields."""
    missing: list[tuple[str, str, list[str]]] = []
    for path in sorted(iter_grit_files(grit_patterns_dir)):
        with open(path, 'r') as f:
            lines = f.readlines()
        pending_meta: dict = {}
        for line in lines:
            m_line = _RULE_LINE_RE.match(line)
            if m_line:
                pending_meta[m_line.group(1)] = m_line.group(2)
                continue
            m_decl = _PATTERN_DECL_RE.match(line)
            if m_decl:
                missing_fields = [f for f in REQUIRED_FIELDS if f not in pending_meta]
                if missing_fields:
                    rel = os.path.relpath(path, project_root)
                    missing.append((rel, m_decl.group(1), missing_fields))
                pending_meta = {}
                continue
            if line.strip() and not line.lstrip().startswith('//'):
                pending_meta = {}
    return missing


__all__ = [
    "REQUIRED_FIELDS",
    "RuleMetadataError",
    "iter_grit_files",
    "parse_file",
    "parse_grit_rules",
    "missing_metadata",
]
