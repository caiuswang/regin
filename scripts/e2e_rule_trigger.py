#!/usr/bin/env python3
"""E2E: real Claude session must populate the rule_triggers history.

Spawns a real `claude -p` session, has it create a Vue file with a
`window.confirm` call (violates `avoid_native_confirm_dialogs`). The
PostToolUse hook wired in `~/.claude/settings.json` runs the
`hook_manager` rule_check handler, which is supposed to POST one
event per applicable rule to `/api/rule-triggers`.

After the session ends, the script queries `/api/triggers` for the
session and reports whether the row landed.

Prereqs:
- `claude` on PATH (user auth already done — uses `--setting-sources user`)
- regin Flask server running on $REGIN_INGEST_BASE_URL (default 127.0.0.1:8321)
- ~/.claude/settings.json with PostToolUse → hook_manager wired

Exit 0 means at least one rule_trigger row was recorded for the spawned
session. Exit 1 means the regression is still present.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / 'frontend'
TARGET_VUE = FRONTEND_ROOT / 'src' / '__e2e_trigger_probe__.vue'
INGEST_BASE = os.environ.get('REGIN_INGEST_BASE_URL', 'http://127.0.0.1:8321').rstrip('/')


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _seed_target_file() -> None:
    TARGET_VUE.parent.mkdir(parents=True, exist_ok=True)
    atexit.register(_cleanup_target_file)
    TARGET_VUE.write_text(
        '<script setup>\n'
        '// E2E probe — edited by claude during real-session test.\n'
        'function placeholder() { return 1 }\n'
        '</script>\n'
        '<template><div /></template>\n'
    )


def _cleanup_target_file() -> None:
    try:
        TARGET_VUE.unlink()
    except FileNotFoundError:
        pass


def _spawn_claude(probe_marker: str) -> str:
    rel = TARGET_VUE.relative_to(REPO_ROOT)
    prompt = (
        f'Use the Edit tool to modify the file `{rel}`. Replace the '
        f'`function placeholder()` body so the function calls `window.confirm("{probe_marker}")` '
        f'and returns its result. Do not add any other changes. Do not run any tests. '
        f'Reply with only "done" when the edit is applied.'
    )
    cmd = [
        'claude', '-p', prompt,
        '--setting-sources', 'user',
        '--output-format', 'stream-json',
        '--verbose',
        '--model', 'haiku',
        '--dangerously-skip-permissions',
        '--no-session-persistence',
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL,
    )
    session_id = ''
    for line in (proc.stdout or '').splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = ev.get('session_id') or ev.get('sessionId')
        if sid:
            session_id = sid
            break
    if not session_id:
        sys.stderr.write('claude session_id not found in stream output\n')
        sys.stderr.write(f'stdout tail: {(proc.stdout or "")[-500:]}\n')
        sys.stderr.write(f'stderr tail: {(proc.stderr or "")[-500:]}\n')
    return session_id


def _fetch_triggers(session_id: str) -> list[dict]:
    """Read /api/triggers?session=<id>. The PostToolUse hook posts events
    synchronously before claude returns, so a single GET after the
    subprocess exits is enough; one short retry covers Flask write-flush lag."""
    url = f'{INGEST_BASE}/api/triggers?session={session_id}'
    for attempt in range(2):
        try:
            envelope = _http_get_json(url)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            sys.stderr.write(f'/api/triggers failed: {exc}\n')
            return []
        rows = envelope.get('items') or []
        if rows or attempt == 1:
            return rows
        time.sleep(0.5)
    return []


def main() -> int:
    probe = f'e2e-{uuid.uuid4().hex[:8]}'
    print(f'probe marker: {probe}')
    print(f'target file:  {TARGET_VUE}')
    print(f'ingest base:  {INGEST_BASE}')
    _seed_target_file()
    try:
        session_id = _spawn_claude(probe)
        print(f'spawned claude session: {session_id or "<unknown>"}')
        if not session_id:
            return 1
        # Hook fires synchronously after the Edit tool; the POST is best-effort with
        # retries, so give it a moment to land.
        rows = _fetch_triggers(session_id)
        if not rows:
            print('FAIL: no rule_triggers row recorded for the session.')
            return 1
        print(f'PASS: {len(rows)} rule_trigger row(s) recorded:')
        for r in rows:
            print(f"  - {r['rule_id']} match_count={r['match_count']} file={r['file_path']}")
        return 0
    finally:
        _cleanup_target_file()


if __name__ == '__main__':
    sys.exit(main())
