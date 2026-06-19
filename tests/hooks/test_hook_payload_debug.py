"""Tests for scripts/hook_payload_debug.py.

The script is installed as a parallel hook for UserPromptSubmit, PreToolUse,
and PostToolUse in settings.json. It appends every payload it receives to
~/.claude/hook-payloads.jsonl and is the log that tests/trace/harness.py
uses to detect Claude's idle state. Getting the JSONL format wrong or the
response shape wrong silently breaks both observability and the trace
harness — these tests pin both contracts.

Isolation strategy: the script resolves its log path with
`os.path.expanduser('~/.claude/hook-payloads.jsonl')`, so a per-test HOME
env override points it at a tmp directory — no monkeypatching of an already
imported module needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / 'scripts' / 'hook_payload_debug.py'


def _run(payload: str | None, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the script with `payload` on stdin and HOME pointing at `home`."""
    env = {**os.environ, 'HOME': str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload if payload is not None else '',
        capture_output=True,
        text=True,
        env=env,
    )


def _read_log(home: Path) -> list[dict]:
    log = home / '.claude' / 'hook-payloads.jsonl'
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


# --- stdout contract: response shape ----------------------------------------

def test_pretooluse_response_injects_no_context(tmp_path):
    """The debugger fires on every event, so it must NOT return
    `additionalContext` — echoing a sentinel back would inject a junk line
    into the model's context on every tool call. Its job is the log file;
    stdout is suppressed and carries no hookSpecificOutput."""
    payload = json.dumps({
        'hook_event_name': 'PreToolUse',
        'session_id': 's1',
        'tool_name': 'Read',
    })
    r = _run(payload, tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out['suppressOutput'] is True
    assert 'hookSpecificOutput' not in out


def test_userpromptsubmit_response_injects_no_context(tmp_path):
    payload = json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 's1'})
    r = _run(payload, tmp_path)
    out = json.loads(r.stdout)
    assert out['suppressOutput'] is True
    assert 'hookSpecificOutput' not in out


def test_posttooluse_response_injects_no_context(tmp_path):
    payload = json.dumps({'hook_event_name': 'PostToolUse', 'session_id': 's1', 'tool_name': 'Edit'})
    r = _run(payload, tmp_path)
    out = json.loads(r.stdout)
    assert out['suppressOutput'] is True
    assert 'hookSpecificOutput' not in out


def test_sessionstart_response_omits_specific_output(tmp_path):
    """SessionStart / SessionEnd / Notification / Stop etc. either reject or
    ignore hookSpecificOutput in older Claude Code versions. The script
    defensively suppresses it — if someone widens the set, make them update
    this test deliberately."""
    payload = json.dumps({'hook_event_name': 'SessionStart', 'session_id': 's1'})
    r = _run(payload, tmp_path)
    out = json.loads(r.stdout)
    assert out['suppressOutput'] is True
    assert 'hookSpecificOutput' not in out


def test_unknown_event_response_omits_specific_output(tmp_path):
    """Made-up event shouldn't crash — response is minimal, log still
    receives an entry."""
    payload = json.dumps({'hook_event_name': 'MadeUpEvent'})
    r = _run(payload, tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert 'hookSpecificOutput' not in out


# --- stdin handling ----------------------------------------------------------

def test_empty_stdin_does_not_crash(tmp_path):
    r = _run('', tmp_path)
    assert r.returncode == 0, r.stderr
    # Empty JSON still logs an Unknown entry with _raw_error set.
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]['hook_event'] == 'Unknown'
    assert entries[0]['payload'].get('_raw_error')


def test_malformed_json_stdin_logs_raw_error(tmp_path):
    r = _run('this is not json', tmp_path)
    assert r.returncode == 0, r.stderr
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]['hook_event'] == 'Unknown'
    assert '_raw_error' in entries[0]['payload']


# --- log-file contract ------------------------------------------------------

def test_log_entry_captures_full_payload(tmp_path):
    """Every hook payload we log has to preserve the raw JSON so downstream
    tooling (tests/trace/harness.py, the web dashboard) can read any field
    out of it. Don't drop fields on the way into the log."""
    payload_obj = {
        'hook_event_name': 'PreToolUse',
        'session_id': 'abc123',
        'tool_name': 'Bash',
        'tool_input': {'command': 'ls', 'description': 'list'},
        'extra_field': {'nested': [1, 2, 3]},
    }
    r = _run(json.dumps(payload_obj), tmp_path)
    assert r.returncode == 0, r.stderr
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e['hook_event'] == 'PreToolUse'
    assert e['session_id'] == 'abc123'
    # received_at must be present and parse-able as ISO; pytest-free check.
    assert 'T' in e['received_at']
    # The original payload must round-trip without loss.
    assert e['payload'] == payload_obj


def test_log_is_append_only_across_invocations(tmp_path):
    """The trace harness relies on being able to `tail -c +$OFFSET` the
    log across sequential turns. Each invocation must append one line,
    never truncate or rewrite."""
    for i in range(3):
        payload = json.dumps({'hook_event_name': 'PreToolUse', 'session_id': f's{i}'})
        r = _run(payload, tmp_path)
        assert r.returncode == 0, r.stderr
    entries = _read_log(tmp_path)
    assert [e['session_id'] for e in entries] == ['s0', 's1', 's2']


def test_missing_claude_dir_is_created(tmp_path):
    """Fresh HOME — no ~/.claude yet. The script must create the
    directory tree; if it doesn't, logging silently fails and every
    hook invocation loses its payload."""
    assert not (tmp_path / '.claude').exists()
    payload = json.dumps({'hook_event_name': 'PreToolUse', 'session_id': 's1'})
    r = _run(payload, tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / '.claude' / 'hook-payloads.jsonl').exists()


def test_log_lines_are_valid_jsonl(tmp_path):
    """Mix in a payload with unicode + nested structures — each output line
    must parse as standalone JSON. One malformed line would poison every
    reader that uses `for line in file: json.loads(line)`."""
    payloads = [
        {'hook_event_name': 'PreToolUse', 'note': 'ascii'},
        {'hook_event_name': 'PostToolUse', 'note': 'unicode: 日本語 / émoji 🌟'},
        {'hook_event_name': 'UserPromptSubmit', 'note': 'newlines\nin\nfields'},
    ]
    for p in payloads:
        r = _run(json.dumps(p), tmp_path)
        assert r.returncode == 0
    raw = (tmp_path / '.claude' / 'hook-payloads.jsonl').read_text()
    lines = [ln for ln in raw.splitlines() if ln]
    assert len(lines) == 3
    for ln in lines:
        # Each line must be a complete JSON object — no embedded newlines
        # bleeding across line boundaries.
        json.loads(ln)


def test_hook_event_falls_back_to_unknown(tmp_path):
    """If a future Claude Code version sends a payload without
    hook_event_name, we should still log it — just with 'Unknown'."""
    payload = json.dumps({'session_id': 's1', 'tool_name': 'Read'})
    r = _run(payload, tmp_path)
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]['hook_event'] == 'Unknown'
    out = json.loads(r.stdout)
    # Unknown isn't in the allow-list → no hookSpecificOutput.
    assert 'hookSpecificOutput' not in out
