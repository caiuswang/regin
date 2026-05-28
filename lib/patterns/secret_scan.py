"""Secret-pattern detection — shared by the pre-commit hook, the fixture-
safety pytest check, and the capture-fixtures helper.

Patterns stay narrow: false positives make developers disable the checks.
"""
from __future__ import annotations

import os
import re

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), 'OpenAI/DeepSeek-style API key'),
    (re.compile(r'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'), 'JWT'),
    (re.compile(r'ghp_[a-zA-Z0-9]{36}'), 'GitHub personal access token'),
    (re.compile(r'github_pat_[a-zA-Z0-9_]{20,}'), 'GitHub fine-grained PAT'),
    (re.compile(r'xox[baprs]-[a-zA-Z0-9-]{10,}'), 'Slack token'),
    (re.compile(r'AKIA[A-Z0-9]{16}'), 'AWS access key ID'),
    (re.compile(r'AIza[0-9A-Za-z_-]{35}'), 'Google API key'),
]

ALLOWED = {'sk-REDACTED', 'REDACTED', 'REDACTED-JWT'}

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'dist', 'build',
             'web/static/dist', 'frontend/node_modules'}

SKIP_EXTS = {'.lock', '.min.js', '.map', '.png', '.jpg', '.jpeg', '.gif',
             '.pdf', '.zip', '.tar', '.gz', '.db', '.sqlite'}


def should_scan(path: str) -> bool:
    parts = path.split(os.sep)
    if any(p in SKIP_DIRS for p in parts):
        return False
    if os.path.splitext(path)[1] in SKIP_EXTS:
        return False
    if path.endswith('package-lock.json') or path.endswith('yarn.lock'):
        return False
    return True


def scan_text(content: str) -> list[tuple[str, str, int]]:
    """Return [(label, preview, line_no), ...] for every secret hit."""
    hits = []
    for pat, label in PATTERNS:
        for m in pat.finditer(content):
            if m.group(0) in ALLOWED:
                continue
            line_no = content.count('\n', 0, m.start()) + 1
            hits.append((label, m.group(0)[:16] + '…', line_no))
    return hits


def scan_file(path: str) -> list[tuple[str, str, int]]:
    if not should_scan(path):
        return []
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            return scan_text(f.read())
    except (OSError, IsADirectoryError):
        return []
