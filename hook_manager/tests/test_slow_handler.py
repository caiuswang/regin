"""Tests for the slow-handler observability added in iteration 2."""

import io
import json
import time

import pytest

from hook_manager.core import Handler, HookPayload, HookResponse
from hook_manager import runner as runner_mod
from hook_manager.runner import run


def _slow_handler(ms: int):
    def fn(p: HookPayload) -> HookResponse:
        time.sleep(ms / 1000)
        return HookResponse(additional_context='done')
    return fn


def test_slow_handler_logs_to_error_file(tmp_path, monkeypatch):
    log = tmp_path / 'hook-errors.jsonl'
    # `_ERROR_LOG` is only the fallback path; `_error_log_path` first
    # tries the payload's resolved provider traces_dir. Override the
    # resolver directly so the test deterministically writes to tmp.
    monkeypatch.setattr(runner_mod, '_error_log_path', lambda _p=None: str(log))
    monkeypatch.setattr(runner_mod, '_SLOW_HANDLER_MS', 50)

    slow = Handler(name='slow', events=['*'], kind='trace', fn=_slow_handler(150))
    out = io.StringIO()
    rc = run('Stop', [slow], '{"hook_event_name":"Stop"}', out)
    assert rc == 0
    # The log should contain exactly one SlowHandler entry
    assert log.exists()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    slow_entries = [e for e in entries if e.get('error_type') == 'SlowHandler']
    assert len(slow_entries) == 1
    assert slow_entries[0]['handler'] == 'slow'
    assert slow_entries[0]['event'] == 'Stop'
    assert slow_entries[0]['elapsed_ms'] >= 100


def test_fast_handler_does_not_log(tmp_path, monkeypatch):
    log = tmp_path / 'hook-errors.jsonl'
    monkeypatch.setattr(runner_mod, '_error_log_path', lambda _p=None: str(log))
    monkeypatch.setattr(runner_mod, '_SLOW_HANDLER_MS', 500)

    fast = Handler(name='fast', events=['*'], kind='trace',
                   fn=lambda p: HookResponse(additional_context='quick'))
    out = io.StringIO()
    run('Stop', [fast], '{"hook_event_name":"Stop"}', out)
    # No entries at all
    if log.exists():
        assert log.read_text().strip() == ''


def test_slow_and_failing_handler_logs_both(tmp_path, monkeypatch):
    log = tmp_path / 'hook-errors.jsonl'
    monkeypatch.setattr(runner_mod, '_error_log_path', lambda _p=None: str(log))
    monkeypatch.setattr(runner_mod, '_SLOW_HANDLER_MS', 50)

    def slow_then_raise(p):
        time.sleep(0.15)
        raise RuntimeError('boom')

    h = Handler(name='buggy', events=['*'], kind='trace', fn=slow_then_raise)
    out = io.StringIO()
    run('Stop', [h], '{"hook_event_name":"Stop"}', out)

    entries = [json.loads(line) for line in log.read_text().splitlines()]
    kinds = sorted(e['error_type'] for e in entries)
    assert 'RuntimeError' in kinds
    assert 'SlowHandler' in kinds
