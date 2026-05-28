#!/usr/bin/env python3
"""Scan files for leaked secrets.

Used as a pre-commit hook via .githooks/pre-commit. Exits nonzero if any
high-signal secret pattern is found in the given files (or, with
`--staged`, in files staged for commit).

This exists because hook fixtures have historically captured real
settings.json content verbatim — including env-var values (API keys,
webhook tokens, JWTs) — into committed test data.

Override for an intentional case:
    SKIP_SECRET_SCAN=1 git commit ...
"""
from __future__ import annotations

import os
import subprocess
import sys

from lib.patterns.secret_scan import scan_file


def _changed_files() -> list[str]:
    out = subprocess.check_output(
        ['git', 'diff', '--cached', '--name-only', '--diff-filter=AM'],
        text=True,
    )
    return [p for p in out.splitlines() if p.strip()]


def main() -> int:
    if os.environ.get('SKIP_SECRET_SCAN') == '1':
        return 0
    args = sys.argv[1:]
    files = _changed_files() if '--staged' in args else args
    if not files:
        return 0
    bad: dict[str, list[tuple[str, str, int]]] = {}
    for f in files:
        h = scan_file(f)
        if h:
            bad[f] = h
    if not bad:
        return 0
    print('ERROR: possible secrets detected:', file=sys.stderr)
    for f, hits in bad.items():
        for label, preview, line in hits:
            print(f'  {f}:{line}  {label}  ({preview})', file=sys.stderr)
    print(
        '\nRedact the values (e.g. "REDACTED") or, if this is a false '
        'positive, set SKIP_SECRET_SCAN=1 for this commit.',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    sys.exit(main())
