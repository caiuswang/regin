"""BundleEngine — generic adapter for pattern-bundle rule packs.

A bundle is a directory under `settings.patterns_dir` whose root contains
a `regin-bundle.yaml` manifest (see `lib.rule_engines.manifest`). The
engine reads the manifest, loads rule files from `<root>/<rules_dir>/`,
and dispatches `run()` calls to the bundle's runner via a JSON-over-stdin
contract that matches the one already used by `frontend-ux-runner.mjs`:

    stdin:  {"repo_root": str, "file_path": str, "rule": {...}}
    stdout: {"matches": int, "details": [str, ...]}

The runner may be written in Node, Python, or Bash — `manifest.runner.kind`
selects the interpreter. Beyond that, the engine is intentionally generic:
it doesn't know what the bundle's checkers do, only how to feed them a
payload and parse the result.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from lib.rule_engines.base import (
    ApplicableRules, Rule, Violation, default_applicable_rules,
)
from lib.rule_engines.manifest import (
    BundleManifest,
    MANIFEST_NAMES,
    resolve_runner_entry,
)


class BundleEngine:
    """Rule-engine adapter that executes bundle-provided checker scripts."""

    kind = 'bundle'

    def __init__(self, *, id: str, bundle_root: str | Path,
                 manifest: BundleManifest,
                 project_root: str | Path | None = None,
                 external_runner_path: str | Path | None = None) -> None:
        self.id = id
        self.bundle_root = Path(bundle_root).resolve()
        self.manifest = manifest
        self.language_ids = tuple(manifest.language_ids)
        # `project_root` is what parse-time `Rule.source_file` is computed
        # relative to. For a bundle living under the user's regin patterns
        # dir, the bundle root is the natural anchor — rules are global,
        # the editing repo's path is irrelevant at parse time. At run
        # time, `run()` receives `repo_root` from the hook directly.
        self.project_root = str(Path(project_root).resolve()) if project_root else str(self.bundle_root)
        # When set, `run()` uses this runner path verbatim instead of resolving
        # `manifest.runner.entry` relative to `bundle_root`. This is the seam
        # for a bundle whose runner lives outside the bundle layout it was
        # constructed for.
        self.external_runner_path = (
            Path(external_runner_path).resolve() if external_runner_path else None
        )

    # ── Paths ──────────────────────────────────────────────────────────

    @property
    def rules_dir(self) -> Path:
        return self.bundle_root / self.manifest.rules_dir

    @property
    def checkers_dir(self) -> Path:
        return self.bundle_root / self.manifest.checkers_dir

    # ── Rule parsing ───────────────────────────────────────────────────

    # Filenames at the bundle root that aren't rule files — the bundle's
    # own manifest plus JSON manifests for adjacent tooling. Skipped during
    # parse so a bundle can keep its rule YAMLs at the root (`rules_dir: '.'`)
    # without slurping `package.json` or `regin-bundle.yaml` as rules.
    _RESERVED_NAMES = frozenset(
        {'package.json', 'bundle.json', 'package-lock.json'} | set(MANIFEST_NAMES)
    )
    # Directory names skipped during rule discovery. Bundle authors keep
    # dependencies under these conventional dirs and would otherwise see
    # hundreds of irrelevant JSON files walked on every parse.
    _SKIP_DIR_NAMES = frozenset({'node_modules', '.git', '__pycache__'})

    def parse_rules(self) -> list[Rule]:
        if not self.rules_dir.is_dir():
            return []
        rules: list[Rule] = []
        seen: set[str] = set()
        for path in self._iter_rule_files():
            for raw in self._load_rule_file(path):
                if raw.get('disabled'):
                    continue
                rule_id = raw.get('id')
                if not rule_id or rule_id in seen:
                    continue
                seen.add(rule_id)
                rules.append(self._rule_from_dict(raw, path))
        return rules

    def _iter_rule_files(self):
        """Yield rule-file paths under `rules_dir`, skipping `node_modules` /
        `.git` / `__pycache__` and the reserved root-level filenames.
        Walks lazily so a bundle with a huge `node_modules/` doesn't pay
        the cost of `rglob('*')` materialising every file."""
        stack = [self.rules_dir]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name in self._SKIP_DIR_NAMES or entry.name.startswith('.'):
                        continue
                    stack.append(entry)
                    continue
                if entry.suffix.lower() not in ('.yaml', '.yml', '.json'):
                    continue
                if entry.name in self._RESERVED_NAMES:
                    continue
                yield entry

    def _load_rule_file(self, path: Path) -> list[dict]:
        text = path.read_text(encoding='utf-8')
        data = (
            json.loads(text) if path.suffix.lower() == '.json'
            else yaml.safe_load(text)
        )
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def _rule_from_dict(self, data: dict, path: Path) -> Rule:
        metadata = {
            'checker': data.get('checker'),
            'options': data.get('options') or {},
            'rationale': data.get('rationale', ''),
            'fix_hint': data.get('fix_hint', ''),
            'guide': data.get('guide'),
            'content_triggers': tuple(data.get('content_triggers') or ()),
            'bundle_id': self.id,
        }
        # Forward any unrecognised keys into metadata so checkers can read
        # bundle-specific fields without the engine knowing about them.
        for key, value in data.items():
            if key in metadata or key in ('id', 'summary', 'severity', 'triggers', 'disabled'):
                continue
            metadata[key] = value
        try:
            source_file = os.path.relpath(path, self.project_root)
        except ValueError:
            source_file = str(path)
        return Rule(
            id=data['id'],
            engine=self.id,
            summary=data.get('summary', data['id']),
            severity=data.get('severity', self.manifest.severity_default),
            triggers=tuple(data.get('triggers') or ()),
            source_file=source_file,
            metadata=metadata,
        )

    # ── Applicability ──────────────────────────────────────────────────

    def applies_to(self, rule: Rule | dict, file_path: str,
                   content: str) -> bool:
        rule_dict = self._as_dict(rule)
        triggers = tuple(rule_dict.get('triggers') or ())
        content_triggers = tuple(rule_dict.get('content_triggers') or ())
        if not triggers and not content_triggers:
            return False
        if triggers and not _glob_match(triggers, file_path):
            return False
        if content_triggers and not any(
            _content_match(t, content) for t in content_triggers
        ):
            return False
        return True

    # ── Applicable rules (default impl) ────────────────────────────────

    def applicable_rules(self, file_path: str, content: str) -> ApplicableRules:
        return default_applicable_rules(self, file_path, content)

    # ── Execution ──────────────────────────────────────────────────────

    def run(self, rule: Rule | dict, file_path: str,
            repo_root: str) -> Violation | None:
        rule_dict = self._as_dict(rule)
        if self.external_runner_path is not None:
            if not self.external_runner_path.is_file():
                return None
            entry = self.external_runner_path
        else:
            try:
                entry = resolve_runner_entry(self.bundle_root, self.manifest.runner.entry)
            except ValueError:
                return None
        argv = self._argv_for(entry)
        payload = {
            'repo_root': repo_root,
            'file_path': file_path,
            'rule': {
                'id': rule_dict.get('id'),
                'checker': rule_dict.get('checker'),
                'options': rule_dict.get('options') or {},
                'metadata': {
                    k: v for k, v in rule_dict.items()
                    if k not in ('id', 'summary', 'severity', 'triggers', 'source_file')
                },
            },
        }
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.bundle_root),
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.manifest.runner.timeout_seconds,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        try:
            result = json.loads(proc.stdout or '{}')
        except json.JSONDecodeError:
            return None
        matches = int(result.get('matches') or 0)
        if matches <= 0:
            return None
        details = result.get('details') or []
        detail = '; '.join(str(d) for d in details[:3]) if details else None
        return Violation(
            rule_id=rule_dict['id'],
            file_path=file_path,
            match_count=matches,
            detail=detail,
        )

    def _argv_for(self, entry: Path) -> list[str]:
        kind = self.manifest.runner.kind
        if kind == 'node':
            return ['node', str(entry)]
        if kind == 'python':
            return [sys.executable, str(entry)]
        if kind == 'shell':
            return ['bash', str(entry)]
        # Unreachable — pydantic restricts `kind` to the three values above.
        raise ValueError(f'unsupported runner kind: {kind!r}')

    # ── Skill / index plumbing ────────────────────────────────────────

    def contributed_skills(self) -> list[dict]:
        """Bundle engines never contribute auto-skills.

        The bundle directory IS the pattern directory; `lib.skills.skill_registry`'s
        pattern walk already deploys the bundle's `SKILL.md` as a regular
        pattern-type skill. Returning anything here would pre-empt that path
        and break SKILL deployment.
        """
        return []

    @classmethod
    def reserved_auto_skill_ids(cls) -> frozenset[str]:
        return frozenset()

    def write_index(self) -> dict:
        rules = self.parse_rules()
        return {
            'engine': self.id,
            'kind': self.kind,
            'rules': len(rules),
            'bundle_root': str(self.bundle_root),
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _as_dict(rule: Rule | dict) -> dict:
        if isinstance(rule, Rule):
            return {
                'id': rule.id,
                'summary': rule.summary,
                'severity': rule.severity,
                'triggers': list(rule.triggers),
                'source_file': rule.source_file,
                **rule.metadata,
            }
        return rule


def _glob_match(globs: tuple[str, ...], file_path: str) -> bool:
    normalized = file_path.replace(os.sep, '/')
    candidates = {normalized, os.path.basename(normalized)}
    if not normalized.startswith('src/'):
        candidates.add(f'src/{normalized}')
    for trig in globs:
        for pattern in _expand_glob(trig):
            for cand in candidates:
                if fnmatch.fnmatch(cand, pattern):
                    return True
    return False


def _expand_glob(trigger: str) -> tuple[str, ...]:
    if '/**/' in trigger:
        return (trigger, trigger.replace('/**/', '/'))
    return (trigger,)


def _content_match(trigger: str, content: str) -> bool:
    if trigger.startswith('@'):
        return trigger in content
    return re.search(r'\b' + re.escape(trigger) + r'\b', content) is not None
