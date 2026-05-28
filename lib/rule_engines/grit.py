"""GritEngine — GritQL adapter implementing lib.rule_engines.base.RuleEngine.

The existing grit integration's pure-parse helpers live in
`lib.utils.grit_parser` and its filesystem-heavy orchestrator in
`lib.rules.grit_rule_index`; this adapter is the facade that the rest of regin
(hook_manager, sync engine, blueprints) now talks to instead of reaching
into those modules directly. Behaviour is intentionally identical — the
adapter is a re-packaging, not a rewrite. Phase 4 will tighten the path
conventions it owns; Phase 5 will wire it into the settings registry.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from typing import Iterable

from lib import languages
from lib.rule_engines.base import ApplicableRules, Rule, Violation
from lib.utils import grit_parser


# Environment override for tests: force a specific grit binary path.
_GRIT_BIN_ENV = 'HOOK_MANAGER_GRIT_BIN'
_PER_RULE_TIMEOUT_S = 20


def _find_grit_dir(start_file: str) -> str | None:
    """Walk up from `start_file` looking for a `.grit` directory."""
    cur = os.path.dirname(os.path.abspath(start_file))
    while cur and cur != os.path.dirname(cur):
        candidate = os.path.join(cur, '.grit')
        if os.path.isdir(candidate):
            return candidate
        cur = os.path.dirname(cur)
    return None


def _load_rule_dicts(rules_path: str) -> list[dict]:
    """Load the indexed grit rule list from disk. Reading the
    pre-built index is much faster than re-parsing `.grit` sources on
    every hook invocation."""
    if not os.path.isfile(rules_path):
        return []
    try:
        with open(rules_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in data.get('rules', []) if not r.get('disabled')]


class GritEngine:
    """GritQL rule-engine adapter."""

    kind = 'grit'

    def __init__(self, grit_dir: str, *, id: str = 'grit',
                 language_ids: tuple[str, ...] = ('java',),
                 project_root: str | None = None) -> None:
        self.id = id
        self.grit_dir = grit_dir
        self.language_ids = tuple(language_ids)
        # project_root is the path the grit parser uses to compute relative
        # `source_file` values. Defaults to the parent of grit_dir, matching
        # the legacy convention where `.grit/` sits next to `patterns/` etc.
        self.project_root = project_root or os.path.dirname(grit_dir)

    # ── Path layout ────────────────────────────────────────────

    def patterns_dir(self, language_id: str) -> str:
        """Directory holding `.grit` source files for a given language."""
        return os.path.join(self.grit_dir, 'patterns', language_id)

    # ── Rule parsing ───────────────────────────────────────────

    def parse_rules(self) -> list[Rule]:
        """Load every enabled rule this engine owns, across its languages."""
        rules: list[Rule] = []
        for language_id in self.language_ids:
            raw = grit_parser.parse_grit_rules(
                self.patterns_dir(language_id), self.project_root,
            )
            for d in raw:
                rules.append(self._rule_from_dict(d, language_id))
        return rules

    def _rule_from_dict(self, d: dict, language_id: str) -> Rule:
        return Rule(
            id=d['id'],
            engine=self.id,
            summary=d['summary'],
            severity=d['severity'],
            triggers=tuple(d['triggers']),
            source_file=d['source_file'],
            metadata={
                'layer': d['layer'],
                'guide': d['guide'],
                'language': language_id,
                'disabled': d.get('disabled', False),
            },
        )

    # ── Applicability / matching ───────────────────────────────

    def applies_to(self, rule: Rule | dict, file_path: str,
                   file_content: str) -> bool:
        """Return True if `rule`'s triggers select `file_path`.

        Mirrors the legacy matcher exactly:
        - triggers containing `*` or ending in a known file extension are
          filename globs (basename match, OR across globs)
        - everything else is a content trigger (annotation or bareword,
          OR across triggers, AND across kinds)
        """
        rule_dict = self._as_dict(rule)
        language_id = self._language_of(rule)
        extensions = self._extensions_for(language_id)
        filename_globs, content_triggers = self._partition_triggers(
            rule_dict.get('triggers', []), extensions,
        )

        basename = os.path.basename(file_path)
        if filename_globs and not any(fnmatch.fnmatch(basename, g) for g in filename_globs):
            return False
        if content_triggers and not any(
            _content_match(t, file_content) for t in content_triggers
        ):
            return False
        return bool(filename_globs or content_triggers)

    @staticmethod
    def _partition_triggers(triggers: Iterable[str],
                             extensions: tuple[str, ...]) -> tuple[list[str], list[str]]:
        filename_globs: list[str] = []
        content_triggers: list[str] = []
        for trig in triggers:
            if '*' in trig or any(trig.endswith(ext) for ext in extensions):
                filename_globs.append(trig)
            else:
                content_triggers.append(trig)
        return filename_globs, content_triggers

    def language_extensions(self, rule: Rule | dict) -> tuple[str, ...]:
        """File extensions applicable to `rule` (public for callers that
        need to filter a directory walk)."""
        return self._extensions_for(self._language_of(rule))

    def _language_of(self, rule: Rule | dict) -> str:
        if isinstance(rule, Rule):
            return rule.metadata.get('language', self.language_ids[0])
        # Back-compat: legacy dicts don't carry `language`; default to the
        # engine's first registered language (Java today).
        return rule.get('language', self.language_ids[0])

    def _extensions_for(self, language_id: str) -> tuple[str, ...]:
        try:
            return languages.get(language_id).file_extensions
        except KeyError:
            return ()

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

    # ── Repo-local discovery + applicable_rules ────────────────

    def applicable_rules(self, file_path: str, content: str) -> ApplicableRules:
        """Resolve rules from the repo-local `.grit/` dir nearest `file_path`.

        Unlike engines whose rules live at a configured path, grit
        loads rules from the `.grit/` directory walked up from the
        edited file. The pre-built `rules.json` index is read directly
        (re-parsing on every PostToolUse would be slow). When the
        discovered `.grit/` differs from `self.grit_dir`, a fresh
        `GritEngine` is bound for the per-rule `.run()` call so the
        grit CLI is pointed at the right directory.
        """
        grit_dir = _find_grit_dir(file_path)
        if not grit_dir:
            return ApplicableRules(items=[], total_in_pool=0, repo_root=None)
        repo_root = os.path.dirname(grit_dir)
        rule_dicts = _load_rule_dicts(os.path.join(grit_dir, 'rules.json'))
        if not rule_dicts:
            return ApplicableRules(items=[], total_in_pool=0, repo_root=repo_root)
        runner = self if grit_dir == self.grit_dir else GritEngine(
            grit_dir=grit_dir,
            id=self.id,
            language_ids=self.language_ids,
            project_root=repo_root,
        )
        items: list[tuple] = []
        for rule in rule_dicts:
            if not runner.applies_to(rule, file_path, content):
                continue
            items.append((runner, rule, rule.get('guide')))
        return ApplicableRules(
            items=items, total_in_pool=len(rule_dicts), repo_root=repo_root,
        )

    # ── Execution ──────────────────────────────────────────────

    def run(self, rule: Rule | dict, file_path: str,
            repo_root: str) -> Violation | None:
        """Invoke `grit apply <rule> --dry-run <file>` and report matches."""
        rule_id = self._as_dict(rule)['id']
        grit_bin = os.environ.get(_GRIT_BIN_ENV, 'grit')
        rel = os.path.relpath(file_path, repo_root)
        try:
            proc = subprocess.run(
                [grit_bin, 'apply', rule_id, '--dry-run',
                 '--grit-dir', self.grit_dir, rel],
                cwd=repo_root,
                capture_output=True, text=True,
                timeout=_PER_RULE_TIMEOUT_S,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        combined = (proc.stdout or '') + (proc.stderr or '')
        m = re.search(r'(\d+)\s+match', combined)
        if m:
            count = int(m.group(1))
        else:
            count = combined.count(f'[{rule_id}]')
        if count <= 0:
            return None
        return Violation(rule_id=rule_id, file_path=file_path,
                         match_count=count)

    # ── Engine-contributed skills / index writing ──────────────

    def contributed_skills(self) -> list[dict]:
        """The auto-generated `grit-rules` skill this engine owns.

        Phase 3 exposes the shape; Phase 5 wires it into lib.skills.skill_registry.
        """
        return [{
            'id': 'grit-rules',
            'kind': 'auto',
            'engine_id': self.id,
        }]

    @classmethod
    def reserved_auto_skill_ids(cls) -> frozenset[str]:
        return frozenset({'grit-rules'})

    def write_index(self) -> dict:
        """Regenerate rules.json / RULES.md. Delegates to the legacy
        orchestrator for now — Phase 4 moves ownership into the engine."""
        from lib.rules import grit_rule_index
        return grit_rule_index.regenerate(write_guides=False)


def _content_match(trig: str, file_content: str) -> bool:
    """Shared content-trigger matcher: `@Ann` as substring, else bareword regex."""
    if trig.startswith('@'):
        return trig in file_content
    return re.search(r'\b' + re.escape(trig) + r'\b', file_content) is not None
