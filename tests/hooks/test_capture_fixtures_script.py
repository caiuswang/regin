"""Pins `scripts/capture_fixtures.py main()`: one fixture per (event, tool),
envelope unwrapping, preview vs --apply, and the missing-log error."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'capture_fixtures.py'


@pytest.fixture
def capture(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('capture_fixtures_under_test', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'FIXTURE_DIR', tmp_path / 'fixtures')

    def _main(*argv):
        monkeypatch.setattr('sys.argv', ['capture_fixtures.py', *argv])
        return mod.main()
    return _main


def _write_log(path: Path) -> Path:
    records = [
        {'hook_event': 'PostToolUse',
         'payload': {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'n': 1}},
        {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'n': 2},
        {'hook_event': 'Stop', 'payload': {'n': 3}},
        {'payload': 'not-a-dict', 'hook_event_name': 'Notification'},
        {'tool_name': 'NoEvent'},
    ]
    lines = [json.dumps(r) for r in records]
    lines[2:2] = ['', 'not json']
    path.write_text('\n'.join(lines) + '\n')
    return path


def test_missing_log_is_an_error(capture, tmp_path, capsys):
    rc = capture('--log', str(tmp_path / 'absent.jsonl'))
    assert (rc, 'source log not found' in capsys.readouterr().err) == (1, True)


def test_preview_lists_unique_pairs_without_writing(capture, tmp_path, capsys):
    log = _write_log(tmp_path / 'log.jsonl')
    rc = capture('--log', str(log))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == [
        f'sampled 3 unique (event, tool) payloads from {log}',
        '  Notification.json (new)',
        '  PostToolUse-Bash.json (new)',
        '  Stop.json (new)',
        '',
        '(dry-run; pass --apply to write)',
    ]
    assert not (tmp_path / 'fixtures' / 'Stop.json').exists()


def test_apply_writes_first_payload_per_pair(capture, tmp_path, capsys):
    log = _write_log(tmp_path / 'log.jsonl')
    fixtures = tmp_path / 'fixtures'
    fixtures.mkdir()
    (fixtures / 'Stop.json').write_text('{}')
    assert capture('--log', str(log)) == 0
    assert '  Stop.json (overwrite)' in capsys.readouterr().out
    assert capture('--log', str(log), '--apply') == 0
    written = {p.name: json.loads(p.read_text()) for p in sorted(fixtures.glob('*.json'))}
    assert written == {
        'Notification.json': {'hook_event_name': 'Notification',
                              'payload': 'not-a-dict'},
        'PostToolUse-Bash.json': {'hook_event_name': 'PostToolUse',
                                  'tool_name': 'Bash', 'n': 1},
        'Stop.json': {'hook_event_name': 'Stop', 'n': 3},
    }
    assert capsys.readouterr().out.endswith(f'wrote 3 fixture(s) to {fixtures}\n')
