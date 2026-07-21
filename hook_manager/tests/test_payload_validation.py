"""Tests for lib.trace.payload_validation + payload_drift_store.

Covers:
  * fixture regression — every committed schema validates clean
  * the four drift kinds (unknown_field, missing_required, type_mismatch, unknown_tool)
  * MCP wildcard
  * non-PostToolUse events short-circuit
  * record_findings upserts (insert + bump occurrence_count)
  * ratify atomically mutates the schema file and clears the validator cache
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from hook_manager.core import _normalize_payload
from hook_manager.handlers.post_tool_trace import _TOOL_BUILDERS
import lib.orm.engine as _engine
from lib.trace.payload_validation import (
    DriftFinding, _load_schema, validate, validate_event,
)


_FIXTURES = Path(__file__).parent / 'fixtures'
_SCHEMA_DIR = Path(__file__).resolve().parents[2] / 'lib' / 'trace' / 'payload_schemas' / 'claude'
_KNOWN_TOOLS = set(_TOOL_BUILDERS.keys())


def _load_fixture(name: str) -> dict:
    return _normalize_payload(json.loads((_FIXTURES / name).read_text()))


def _bash_fixture() -> dict:
    return _load_fixture('PostToolUse-Bash.json')


# ── Fixture regression ────────────────────────────────────────────


def _coverable_fixtures() -> list[Path]:
    out = []
    for p in sorted(_FIXTURES.glob('PostToolUse-*.json')):
        raw = json.loads(p.read_text())
        tool = raw.get('tool_name', '')
        if tool in _KNOWN_TOOLS or tool.startswith('mcp__'):
            out.append(p)
    return out


@pytest.mark.parametrize('fixture_path', _coverable_fixtures(), ids=lambda p: p.stem)
def test_fixture_validates_clean(fixture_path):
    payload = _normalize_payload(json.loads(fixture_path.read_text()))
    findings = validate(payload['tool_name'], payload)
    assert findings == [], (
        f"{fixture_path.name} should validate against its committed schema; "
        f"got {len(findings)} drift findings: "
        f"{[(f.drift_kind, f.field_path) for f in findings]}"
    )


# ── Drift kinds ───────────────────────────────────────────────────


def test_unknown_field_drift():
    p = _bash_fixture()
    p['tool_input']['injected'] = 'surprise'
    findings = validate('Bash', p)
    kinds = {(f.drift_kind, f.field_path) for f in findings}
    assert ('unknown_field', 'tool_input.injected') in kinds


def test_missing_required_drift():
    p = _bash_fixture()
    del p['tool_input']['command']
    findings = validate('Bash', p)
    paths = {f.field_path for f in findings if f.drift_kind == 'missing_required'}
    assert any('command' in pth for pth in paths)


def test_type_mismatch_drift():
    p = _bash_fixture()
    p['tool_input']['command'] = {'cmd': 'oops'}
    findings = validate('Bash', p)
    kinds = {f.drift_kind for f in findings}
    assert 'type_mismatch' in kinds


def test_unknown_tool_drift():
    findings = validate('BrandNew', {
        'hook_event_name': 'PostToolUse',
        'tool_name': 'BrandNew',
        'tool_input': {},
    })
    assert len(findings) == 1
    assert findings[0].drift_kind == 'unknown_tool'


def test_mcp_wildcard_accepts_any_mcp_tool():
    payload = _load_fixture(
        'PostToolUse-mcp__plugin_playwright_playwright__browser_click.json',
    )
    findings = validate(payload['tool_name'], payload)
    assert findings == []


def test_non_postool_event_returns_empty():
    findings = validate('Bash', {
        'hook_event_name': 'UserPromptSubmit',
        'tool_name': 'Bash',
        'tool_input': {'command': 'ls'},
    })
    assert findings == []


def test_nested_camelcase_does_not_drift():
    """Read.tool_response.file.numLines is camelCase but the validator
    should recognise it as an alias of num_lines and not flag it."""
    payload = _load_fixture('PostToolUse-Read.json')
    findings = validate('Read', payload)
    assert findings == []


# ── Persistence (record_findings upsert) ───────────────────────────


@pytest.fixture
def clean_drift_table():
    # Resolved at call time, not import time: the autouse `tmp_db` fixture
    # monkeypatches `lib.orm.engine.DB_PATH`, and a `from … import DB_PATH`
    # would bind the real path before that patch ever runs — which is how
    # this fixture came to be DELETEing from the developer's live DB.
    conn = sqlite3.connect(_engine.DB_PATH)
    conn.execute('DELETE FROM payload_schema_drift')
    conn.commit()
    yield conn
    conn.execute('DELETE FROM payload_schema_drift')
    conn.commit()
    conn.close()


def test_record_findings_inserts_then_bumps(clean_drift_table, monkeypatch):
    monkeypatch.setenv('CLAUDE_VERSION', '9.9.9-test')
    from lib.trace.claude_version import current_claude_version
    current_claude_version.cache_clear()
    from lib.trace.payload_drift_store import record_findings

    finding = DriftFinding(
        agent='claude', tool_name='Bash', drift_kind='unknown_field',
        field_path='tool_input.x', expected=None, actual_sample='"v1"',
    )
    record_findings([finding], {'hook_event_name': 'PostToolUse'})
    record_findings([finding], {'hook_event_name': 'PostToolUse'})

    rows = clean_drift_table.execute(
        'SELECT agent, tool_name, occurrence_count, claude_version FROM payload_schema_drift'
    ).fetchall()
    assert rows == [('claude', 'Bash', 2, '9.9.9-test')]


# ── Ratify (schema file mutation + cache invalidation) ─────────────


@pytest.fixture(autouse=True)
def isolated_overlay(tmp_path, monkeypatch):
    """Point payload_schemas_overlay_dir at a per-test tmp dir so the
    real user overlay never pollutes assertions and ratify writes never
    leak between tests. Also force diagnostics_enabled=True so handle()
    actually runs the validate pipeline — the production default is OFF
    but every test in this file is about that pipeline. Clears the
    schema cache on entry+exit so each test sees a fresh merge."""
    from lib import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, 'payload_schemas_overlay_dir', tmp_path,
    )
    monkeypatch.setattr(
        settings_mod.settings, 'diagnostics_enabled', True,
    )
    _load_schema.cache_clear()
    yield tmp_path
    _load_schema.cache_clear()


def test_ratify_writes_to_overlay_and_merges(
    clean_drift_table, isolated_overlay, monkeypatch,
):
    monkeypatch.setenv('CLAUDE_VERSION', '7.7.7-test')
    from lib.trace.claude_version import current_claude_version
    current_claude_version.cache_clear()

    from hook_manager.core import HookPayload
    from hook_manager.handlers.trace_payload import handle
    from web.blueprints.schema_drift import _apply_ratify_to_schema, _load_drift_row

    raw = json.loads((_FIXTURES / 'PostToolUse-Bash.json').read_text())
    raw['tool_input']['ratify_test_field'] = 'hello'
    handle(HookPayload.from_stdin_json('PostToolUse', raw))

    row = clean_drift_table.execute(
        "SELECT id FROM payload_schema_drift WHERE field_path = 'tool_input.ratify_test_field'"
    ).fetchone()
    assert row is not None, 'drift row should exist after handle()'

    drift = _load_drift_row(row[0])
    _apply_ratify_to_schema(drift)

    overlay_path = isolated_overlay / 'claude' / 'Bash.schema.json'
    assert overlay_path.is_file(), 'ratify should create overlay file'
    overlay = json.loads(overlay_path.read_text())
    assert 'ratify_test_field' in overlay['properties']['tool_input']['properties']
    assert '7.7.7-test' in overlay['x-claude-versions']

    # The repo-tracked baseline must not be mutated.
    baseline = json.loads((_SCHEMA_DIR / 'Bash.schema.json').read_text())
    assert 'ratify_test_field' not in baseline['properties']['tool_input']['properties']

    # Validator's merged view sees the overlay → re-fire produces no new drift.
    clean_drift_table.execute('DELETE FROM payload_schema_drift')
    clean_drift_table.commit()
    handle(HookPayload.from_stdin_json('PostToolUse', raw))
    leftover = clean_drift_table.execute(
        "SELECT * FROM payload_schema_drift WHERE field_path = 'tool_input.ratify_test_field'"
    ).fetchall()
    assert leftover == []


# ── Schema lineage (kimi inherits claude) ──────────────────────────


def test_kimi_inherits_claude_schema():
    """Kimi shares Claude's tool payload surface, so a kimi payload
    validates against claude's baseline — real field drift, not a
    blanket unknown_tool — with every finding tagged agent='kimi'."""
    clean = _bash_fixture()
    assert validate('Bash', clean, agent='kimi') == []

    drifted = _bash_fixture()
    drifted['tool_input']['kimi_only_field'] = 'x'
    findings = validate('Bash', drifted, agent='kimi')
    assert {(f.agent, f.drift_kind, f.field_path) for f in findings} == {
        ('kimi', 'unknown_field', 'tool_input.kimi_only_field'),
    }


def test_kimi_unknown_tool_still_flags_when_no_lineage_schema():
    """A tool absent from *both* kimi and claude still drifts as unknown_tool."""
    findings = validate('TotallyMadeUp', {
        'hook_event_name': 'PostToolUse',
        'tool_name': 'TotallyMadeUp',
        'tool_input': {},
    }, agent='kimi')
    assert [f.drift_kind for f in findings] == ['unknown_tool']
    assert findings[0].agent == 'kimi'


def test_kimi_ratify_seeds_from_claude_baseline(clean_drift_table, isolated_overlay):
    """Ratifying a kimi unknown_field seeds the overlay from claude's
    inherited baseline and writes to kimi's *own* overlay dir, leaving
    claude's overlay and the repo baseline untouched."""
    from web.blueprints.schema_drift import _apply_ratify_to_schema

    drift = {
        'id': 1, 'agent': 'kimi', 'subject_kind': 'tool', 'tool_name': 'Bash',
        'drift_kind': 'unknown_field', 'field_path': 'tool_input.kimi_field',
        'sample_value': '"v"', 'claude_version': None,
    }
    _apply_ratify_to_schema(drift)

    overlay_path = isolated_overlay / 'kimi' / 'Bash.schema.json'
    assert overlay_path.is_file(), 'ratify should create the kimi overlay'
    overlay = json.loads(overlay_path.read_text())
    assert 'kimi_field' in overlay['properties']['tool_input']['properties']
    # x-claude-versions is claude-specific; kimi overlays must not carry it.
    assert 'x-claude-versions' not in overlay
    assert not (isolated_overlay / 'claude' / 'Bash.schema.json').exists()

    _load_schema.cache_clear()
    drifted = _bash_fixture()
    drifted['tool_input']['kimi_field'] = 'v'
    assert validate('Bash', drifted, agent='kimi') == []


def test_overlay_only_schema_validates(isolated_overlay):
    """Validator works for tools that exist only in the overlay (no baseline)."""
    overlay_path = isolated_overlay / 'claude' / 'CustomTool.schema.json'
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps({
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'type': 'object',
        'additionalProperties': True,
        'properties': {
            'tool_name': {'const': 'CustomTool'},
            'tool_input': {'type': 'object', 'additionalProperties': True,
                           'properties': {'q': {'type': 'string'}}},
        },
    }))
    _load_schema.cache_clear()

    findings = validate('CustomTool', {
        'hook_event_name': 'PostToolUse',
        'tool_name': 'CustomTool',
        'tool_input': {'q': 'hi'},
    })
    assert findings == []


# ── prompt_id is universal envelope metadata (Claude Code 2.1.195+) ──


def test_bash_git_operation_not_flagged_as_drift():
    """The git-commit metadata captured onto Bash spans must also be
    whitelisted in the Bash schema — otherwise every commit floods drift
    with both the camelCase original and the snake alias. Drives the real
    camelCase wire shape through normalization."""
    payload = _normalize_payload({
        'hook_event_name': 'PostToolUse',
        'tool_name': 'Bash',
        'tool_input': {'command': 'git commit -m x'},
        'tool_response': {'gitOperation': {'commit': {'sha': '8e48620', 'kind': 'create'}}},
        'prompt_id': 'pr-1',
    })
    findings = validate('Bash', payload)
    assert findings == [], findings


def test_prompt_id_not_flagged_on_tool_payload():
    """prompt_id rides every PostToolUse payload; it must be treated as
    envelope metadata, not a per-call unknown_field drift."""
    payload = _bash_fixture()
    payload['prompt_id'] = '34a84388-0e06-4cea-b135-2df8c237df3f'
    findings = validate('Bash', payload)
    assert not [f for f in findings if f.field_path == 'prompt_id'], findings


def test_prompt_id_not_flagged_on_hook_event_payload():
    payload = {
        'hook_event_name': 'Stop',
        'session_id': 's', 'transcript_path': 't', 'cwd': 'c',
        'prompt_id': '34a84388-0e06-4cea-b135-2df8c237df3f',
    }
    findings = validate_event('Stop', payload)
    assert not [f for f in findings if f.field_path == 'prompt_id'], findings


# ── New-tool baseline schemas (2.1.198): no unknown_tool, validate clean ──


@pytest.mark.parametrize('tool,sample_input,sample_response', [
    ('Monitor', {'command': 'sleep 1', 'description': 'wait', 'persistent': False,
                 'timeout_ms': 1000}, {'task_id': 't1', 'persistent': False}),
    ('ReportFindings', {'findings': []}, {'count': 0, 'findings': []}),
    ('SendMessage', {'to': 'agt', 'summary': 's', 'message': 'm'},
     {'success': True, 'message': 'ok'}),
    ('ScheduleWakeup', {'delay_seconds': 60, 'reason': 'poll', 'prompt': 'x'}, {}),
    ('StructuredOutput', {'anything': {'nested': 1}}, {}),
])
def test_new_tool_schema_validates_clean(tool, sample_input, sample_response):
    payload = _normalize_payload({
        'hook_event_name': 'PostToolUse',
        'tool_name': tool,
        'tool_input': sample_input,
        'tool_response': sample_response,
        'prompt_id': 'pr-1',
    })
    findings = validate(tool, payload)
    assert findings == [], f'{tool}: {findings}'


# ── New hook-event schemas (compaction lifecycle): no unknown_event ──


@pytest.mark.parametrize('event', ['PreCompact', 'PostCompact'])
def test_compact_event_schema_no_unknown_event(event):
    payload = {
        'hook_event_name': event,
        'session_id': 's', 'transcript_path': 't', 'cwd': 'c',
        'prompt_id': 'pr-1', 'trigger': 'auto',
    }
    findings = validate_event(event, payload)
    assert not [f for f in findings if f.drift_kind == 'unknown_event'], findings
    assert findings == [], findings


# ── Recent scheme drift (Claude Code 2.1.215/2.1.216) whitelisted ──
# Each case drives the real camelCase wire shape through normalization so
# both the snake alias and the camelCase original resolve clean. Mirrors
# `test_bash_git_operation_not_flagged_as_drift`.

@pytest.mark.parametrize('tool,tool_input,tool_response', [
    ('Agent', {'prompt': 'p', 'subagent_type': 'x', 'run_in_background': False}, {}),
    ('Bash', {'command': 'sleep 9', 'dangerouslyDisableSandbox': True},
     {'timedOutAfterMs': 120000, 'dangerouslyDisableSandbox': True,
      'backgroundCwdHint': 'Session cwd remains /repo'}),
    ('SendMessage', {'to': 'a', 'message': 'm'},
     {'success': True, 'pin': {'id': 'aa4f', 'name': 'aa4f', 'ref': '3b97'}}),
    ('Write', {'file_path': '/x', 'content': 'y'},
     {'type': 'create', 'file_path': '/x', 'memdirStamped': True}),
])
def test_recent_field_drift_whitelisted(tool, tool_input, tool_response):
    payload = _normalize_payload({
        'hook_event_name': 'PostToolUse',
        'tool_name': tool,
        'tool_input': tool_input,
        'tool_response': tool_response,
        'prompt_id': 'pr-1',
    })
    findings = validate(tool, payload)
    assert findings == [], findings


def test_permission_denied_reason_and_tool_use_id_clean():
    """PermissionDenied (2.1.215+) carries a human-readable `reason` and the
    `tool_use_id` of the denied call — both must validate against the
    committed baseline, not flag as unknown_field."""
    payload = _normalize_payload({
        'hook_event_name': 'PermissionDenied',
        'session_id': 's', 'transcript_path': 't', 'cwd': 'c',
        'tool_name': 'Bash', 'tool_input': {'command': 'kill 7789'},
        'tool_use_id': 'toolu_014n6WxYqw3FhvPwZNTTeFYv',
        'reason': 'Killing a process the agent did not create risks…',
    })
    findings = validate_event('PermissionDenied', payload)
    assert findings == [], findings
