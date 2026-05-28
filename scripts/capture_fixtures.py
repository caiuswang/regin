#!/usr/bin/env python3
"""Refresh hook_manager/tests/fixtures/ from ~/.claude/hook-payloads.jsonl.

Samples one payload per (hook_event_name, tool_name) pair and writes it
to tests/fixtures/<Event>[-<tool>].json, scrubbing secrets on the way in
so we never re-introduce leaks like the old PostToolUse-Edit.json
settings.json capture.

Scrubbing policy:
  * strip any `env` dict at any depth (Claude Code hook payloads don't
    need it for replay — it exists only as incidental originalFile
    content when a user edits settings.json).
  * redact string values matching PATTERNS in lib/secret_scan.
  * anonymize user home paths so fixtures don't embed personal info.
    `/Users/<name>/...` and `/home/<name>/...` become `/Users/user/...`
    and `/home/user/...` respectively.
  * anonymize UUID-shaped `session_id` values to a fixed
    `test-fixture-session-XXXXXXXX` placeholder, and rewrite the same
    UUID wherever else it appears in the payload (notably transcript
    path filenames). Real session_ids in captured payloads otherwise
    leak into the live regin DB on every replay and pollute the
    user-visible sessions list under that real id.

Usage:
    ./.venv/bin/python scripts/capture_fixtures.py                # preview only
    ./.venv/bin/python scripts/capture_fixtures.py --apply        # write fixtures
    ./.venv/bin/python scripts/capture_fixtures.py --log PATH     # alternate source
    ./.venv/bin/python scripts/capture_fixtures.py --rewrite --apply
        # re-scrub every fixture already on disk (no new sampling)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from lib.patterns.secret_scan import PATTERNS

DEFAULT_SOURCE = Path.home() / '.claude' / 'hook-payloads.jsonl'
FIXTURE_DIR = Path(__file__).resolve().parent.parent / 'hook_manager' / 'tests' / 'fixtures'


def _redact_secrets(s: str) -> str:
    for pat, _label in PATTERNS:
        s = pat.sub('REDACTED', s)
    return s


# Match a filesystem home prefix (`/Users/<name>/`, `/home/<name>/`) OR
# Claude Code's project-id hash encoding (`-Users-<name>-`, `-home-<name>-`).
# The `sep` group captures the surrounding delimiter so we can round-trip it.
_HOME_PATH_RE = re.compile(r'(?P<sep>[/-])(?P<root>Users|home)(?P=sep)\w+(?=(?P=sep))')

# After username anonymization, also collapse the two-segment project
# path that sits under `user` — e.g. `/Users/user/<org>/<repo>` and its
# hashed form `-Users-user-<org>-<repo>` — into a generic
# `<sep>Users<sep>user<sep>project`. Preserves `.claude/` so Claude Code
# transcript paths still resolve under `/Users/user/.claude/projects/...`.
_PROJECT_PATH_RE = re.compile(
    r'(?P<sep>[/-])Users(?P=sep)user(?P=sep)'
    r'(?!\.claude(?P=sep))[\w.-]+(?P=sep)[\w.-]+'
)


def _anonymize_home_paths(s: str) -> str:
    s = _HOME_PATH_RE.sub(lambda m: f"{m['sep']}{m['root']}{m['sep']}user", s)
    s = _PROJECT_PATH_RE.sub(lambda m: f"{m['sep']}Users{m['sep']}user{m['sep']}project", s)
    return s


# Match a canonical UUID (any version, lowercase hex with dashes). Captures the
# id alone so we can swap it inside larger strings (e.g. transcript path
# filenames `<dir>/<uuid>.jsonl`).
_UUID_RE = re.compile(
    r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
)

_FIXTURE_SESSION_PREFIX = 'test-fixture-session-'


def _collect_session_ids(obj, found: set[str]) -> None:
    """Walk the payload and collect every real UUID assigned to a
    `session_id` field. We use the field name (not pattern matching) to
    identify the real ids — anything else that happens to be a UUID
    (tool_use_id, message_id, etc.) is left alone."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'session_id' and isinstance(v, str) and _UUID_RE.fullmatch(v):
                found.add(v)
            _collect_session_ids(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_session_ids(item, found)


def _build_session_id_map(ids: set[str]) -> dict[str, str]:
    """Map each real session_id to a deterministic placeholder so the
    fixture's session_id and any transcript-path occurrence stay
    consistent (replay code can still correlate by id)."""
    return {sid: _FIXTURE_SESSION_PREFIX + sid[:8] for sid in ids}


def _apply_session_id_map(text: str, mapping: dict[str, str]) -> str:
    for real, placeholder in mapping.items():
        text = text.replace(real, placeholder)
    return text


def _scrub(obj):
    """Recursively remove env blocks; redact secrets; anonymize home paths;
    anonymize session_id values (and their occurrences inside other strings)."""
    sessions: set[str] = set()
    _collect_session_ids(obj, sessions)
    mapping = _build_session_id_map(sessions)

    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items() if k != 'env'}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        if isinstance(node, str):
            s = _anonymize_home_paths(_redact_secrets(node))
            if mapping:
                s = _apply_session_id_map(s, mapping)
            return s
        return node

    return _walk(obj)


_SAFE_NAME = re.compile(r'[^A-Za-z0-9._-]')


def _fixture_name(event: str, tool: str | None) -> str:
    base = event if not tool else f'{event}-{tool}'
    return _SAFE_NAME.sub('_', base) + '.json'


def _rewrite_existing_fixtures(apply: bool) -> int:
    """Re-scrub every *.json under FIXTURE_DIR in place (no new sampling)."""
    if not FIXTURE_DIR.is_dir():
        print(f'error: fixture dir not found at {FIXTURE_DIR}', file=sys.stderr)
        return 1
    changed = 0
    total = 0
    for path in sorted(FIXTURE_DIR.glob('*.json')):
        total += 1
        with open(path, encoding='utf-8') as f:
            original_text = f.read()
        try:
            payload = json.loads(original_text)
        except json.JSONDecodeError as exc:
            print(f'  skip {path.name}: {exc}', file=sys.stderr)
            continue
        scrubbed = _scrub(payload)
        new_text = json.dumps(scrubbed, indent=2) + '\n'
        if new_text != original_text:
            changed += 1
            marker = '(would rewrite)' if not apply else '(rewrote)'
            print(f'  {path.name} {marker}')
            if apply:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_text)
    print(f'\n{changed}/{total} fixture(s) {"updated" if apply else "would change"}')
    if not apply:
        print('(dry-run; pass --apply to write)')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log', type=Path, default=DEFAULT_SOURCE,
                   help=f'source jsonl (default: {DEFAULT_SOURCE})')
    p.add_argument('--apply', action='store_true',
                   help='write fixtures (default: preview only)')
    p.add_argument('--rewrite', action='store_true',
                   help='re-scrub existing fixtures on disk (skip sampling)')
    args = p.parse_args()

    if args.rewrite:
        return _rewrite_existing_fixtures(args.apply)

    if not args.log.exists():
        print(f'error: source log not found at {args.log}', file=sys.stderr)
        return 1

    seen: dict[tuple[str, str | None], dict] = {}
    with open(args.log, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # The log wraps each hook payload in a meta envelope. Unwrap
            # if present so fixtures are pure hook payloads (matching the
            # shape the dispatcher reads from stdin).
            payload = record.get('payload') if isinstance(record.get('payload'), dict) else record
            event = payload.get('hook_event_name') or record.get('hook_event')
            if not event:
                continue
            tool = payload.get('tool_name')
            key = (event, tool)
            if key in seen:
                continue
            # Ensure hook_event_name is present on the stored payload.
            if 'hook_event_name' not in payload:
                payload = {'hook_event_name': event, **payload}
            seen[key] = _scrub(payload)

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    to_write: list[tuple[Path, dict]] = []
    for (event, tool), payload in sorted(seen.items()):
        dst = FIXTURE_DIR / _fixture_name(event, tool)
        to_write.append((dst, payload))

    print(f'sampled {len(to_write)} unique (event, tool) payloads from {args.log}')
    for dst, _ in to_write:
        marker = '(new)' if not dst.exists() else '(overwrite)'
        print(f'  {dst.name} {marker}')

    if not args.apply:
        print('\n(dry-run; pass --apply to write)')
        return 0

    for dst, payload in to_write:
        with open(dst, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
            f.write('\n')
    print(f'\nwrote {len(to_write)} fixture(s) to {FIXTURE_DIR}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
