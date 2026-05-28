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
from lib.orm.engine import DB_PATH
from lib.trace.payload_validation import (
    DriftFinding, _load_schema, validate,
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
    conn = sqlite3.connect(DB_PATH)
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
