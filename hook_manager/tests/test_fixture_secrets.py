"""Guardrail: no committed fixture may contain a detectable secret.

The pre-commit hook (scripts/check_secrets.py) catches new additions, but
someone can still bypass it (--no-verify, SKIP_SECRET_SCAN=1, or a branch
pushed from a machine without the hook installed). This test runs in CI
on every PR, so any fixture that drifts back into containing a secret
pattern gets caught before merge.

A leak historically happened when a PostToolUse Edit fixture captured
the full content of ~/.claude/settings.json, including env-block API
keys. Keep the patterns in sync with lib/secret_scan.
"""
from __future__ import annotations

import os

import pytest

from lib.patterns.secret_scan import scan_file

_FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def _fixture_paths() -> list[str]:
    if not os.path.isdir(_FIXTURES):
        return []
    return sorted(
        os.path.join(_FIXTURES, f)
        for f in os.listdir(_FIXTURES)
        if f.endswith('.json')
    )


@pytest.mark.parametrize('fixture_path', _fixture_paths(),
                         ids=lambda p: os.path.basename(p))
def test_fixture_contains_no_secrets(fixture_path: str) -> None:
    hits = scan_file(fixture_path)
    assert not hits, (
        f'Secret pattern(s) detected in {fixture_path}:\n'
        + '\n'.join(f'  line {line}: {label} ({preview})' for label, preview, line in hits)
        + '\nReplace the value with a placeholder like "REDACTED".'
    )
