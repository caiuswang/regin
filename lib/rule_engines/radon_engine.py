"""RadonEngine — Python cyclomatic-complexity adapter implementing
`lib.rule_engines.base.RuleEngine`.

Wraps radon's in-process `cc_visit` / `cc_rank` API. Each engine instance
synthesizes a single `Rule` from its config: any function whose CC grade
is at or above `min_grade` is flagged at the configured `severity`.

Configuration carries through `lib.settings.RuleEngineConfig.min_grade`
and `.severity`; defaults are `'C'` (CC >= 11) and `'warn'`. Two
thresholds = two engine instances (e.g. `radon-warn` + `radon-strict`).

The engine contributes a `python-complexity` auto-skill that documents
the configured threshold for the agent.
"""

from __future__ import annotations

import os

from lib.rule_engines.base import (
    ApplicableRules, Rule, Violation, default_applicable_rules,
)


_AUTO_SKILL_ID = 'python-complexity'
_GUIDE_ID = 'python-complexity'
_DEFAULT_MIN_GRADE = 'C'
_DEFAULT_SEVERITY = 'warn'
_VALID_GRADES = frozenset('ABCDEF')


class RadonEngine:
    """Radon-backed Python complexity rule engine."""

    kind = 'radon'

    def __init__(self, *, id: str = 'radon',
                 language_ids: tuple[str, ...] = ('python',),
                 project_root: str | None = None,
                 min_grade: str = _DEFAULT_MIN_GRADE,
                 severity: str = _DEFAULT_SEVERITY) -> None:
        self.id = id
        self.language_ids = tuple(language_ids)
        self.project_root = project_root or os.getcwd()
        grade = (min_grade or _DEFAULT_MIN_GRADE).upper()
        if grade not in _VALID_GRADES:
            raise ValueError(
                f"radon min_grade must be one of A..F (got {min_grade!r})"
            )
        self.min_grade = grade
        self.severity = severity or _DEFAULT_SEVERITY

    # ── Rule parsing ──────────────────────────────────────────────

    def parse_rules(self) -> list[Rule]:
        """Synthesize a single rule from the configured threshold.

        No on-disk rule files — the engine's config IS the rule source.
        """
        return [
            Rule(
                id=f'python.cyclomatic-complexity.{self.min_grade.lower()}',
                engine=self.id,
                summary=(
                    f'Python function exceeds cyclomatic-complexity grade '
                    f'{self.min_grade}'
                ),
                severity=self.severity,
                triggers=('*.py',),
                source_file='<synthesized>',
                metadata={
                    'language': 'python',
                    'guide': _GUIDE_ID,
                    'min_grade': self.min_grade,
                },
            ),
        ]

    # ── Applicability ─────────────────────────────────────────────

    def applies_to(self, rule: Rule | dict, file_path: str,
                   content: str) -> bool:
        """Match purely by extension. Radon has no annotation-trigger concept."""
        return file_path.endswith('.py')

    # ── Applicable rules (default impl) ───────────────────────────

    def applicable_rules(self, file_path: str, content: str) -> ApplicableRules:
        return default_applicable_rules(self, file_path, content)

    # ── Execution ─────────────────────────────────────────────────

    def run(self, rule: Rule | dict, file_path: str,
            repo_root: str) -> Violation | None:
        """Invoke radon in-process; return one Violation per file that
        exceeds the threshold (match_count = number of offending blocks)."""
        from radon.complexity import cc_visit, cc_rank
        try:
            with open(file_path, encoding='utf-8') as f:
                source = f.read()
        except (OSError, UnicodeDecodeError):
            return None
        try:
            blocks = cc_visit(source)
        except (SyntaxError, ValueError):
            return None

        threshold = ord(self.min_grade) - ord('A')
        offending = [
            b for b in blocks
            if (ord(cc_rank(b.complexity)) - ord('A')) >= threshold
        ]
        if not offending:
            return None

        detail = '; '.join(
            f'{b.name} (CC={b.complexity}, grade={cc_rank(b.complexity)}, '
            f'line {b.lineno})'
            for b in offending[:3]
        )
        rule_id = self._as_dict(rule)['id']
        return Violation(
            rule_id=rule_id, file_path=file_path,
            match_count=len(offending), detail=detail,
        )

    # ── Engine-contributed skills / index writing ─────────────────

    def contributed_skills(self) -> list[dict]:
        return [{
            'id': _AUTO_SKILL_ID,
            'kind': 'auto',
            'engine_id': self.id,
        }]

    @classmethod
    def reserved_auto_skill_ids(cls) -> frozenset[str]:
        return frozenset({_AUTO_SKILL_ID})

    def write_index(self) -> dict:
        return {
            'engine': self.id,
            'kind': self.kind,
            'rules': len(self.parse_rules()),
            'min_grade': self.min_grade,
            'severity': self.severity,
        }

    # ── Helpers ───────────────────────────────────────────────────

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
