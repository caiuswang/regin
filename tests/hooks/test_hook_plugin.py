"""Tests for lib.hook_plugin shared helpers.

These helpers were previously duplicated inline across five hook scripts.
One duplicate had a subtle regex bug — the leading dot in SKILL_READ_RE
was unescaped, so `xclaude/skills/foo/content.md` wrongly matched as a
skill read. These tests lock in the corrected regex and the shape of
each helper.
"""

from __future__ import annotations

import json
import os
import sys

from lib import hook_plugin


# --- skill_id_from_read_path --------------------------------------------

def test_skill_read_matches_canonical_path():
    # The inputs the hook receives are absolute paths; `~` is already
    # expanded because Claude sends real filesystem paths.
    home = '/home/user'
    path = f'{home}/.claude/skills/my-skill/content.md'
    assert hook_plugin.skill_id_from_read_path(path, home=home) == 'my-skill'


def test_skill_read_does_not_match_spoofed_prefix():
    """Regression: previously the regex was `^.claude/skills/…` with an
    unescaped dot, so a path like `Xclaude/skills/foo/content.md` — which
    after home-stripping becomes literally that — wrongly matched."""
    home = '/home/user'
    path = f'{home}/Xclaude/skills/my-skill/content.md'
    assert hook_plugin.skill_id_from_read_path(path, home=home) is None


def test_skill_read_rejects_path_outside_claude_skills():
    home = '/home/user'
    path = f'{home}/.claude/plans/some-plan.md'
    assert hook_plugin.skill_id_from_read_path(path, home=home) is None


def test_skill_read_rejects_path_outside_home():
    # A path that doesn't start with home stays as-is; the regex anchor
    # should not match /etc/.claude/... etc.
    assert hook_plugin.skill_id_from_read_path('/etc/passwd') is None


def test_skill_read_rejects_empty_path():
    assert hook_plugin.skill_id_from_read_path('') is None


# --- normalize_tool_name -------------------------------------------------

def test_normalize_tool_name_replaces_all_unsafe_chars():
    assert (hook_plugin.normalize_tool_name('mcp__foo.bar:baz/qux')
            == 'mcp__foo_bar_baz_qux')


def test_normalize_tool_name_leaves_plain_name_alone():
    assert hook_plugin.normalize_tool_name('Edit') == 'Edit'


# --- extract_file_path ---------------------------------------------------

def test_extract_file_path_prefers_tool_response():
    tool_input = {'file_path': '/tmp/in'}
    tool_response = {'filePath': '/tmp/out'}
    assert hook_plugin.extract_file_path(tool_input, tool_response) == '/tmp/out'


def test_extract_file_path_falls_back_to_tool_input():
    assert hook_plugin.extract_file_path({'file_path': '/tmp/a'}, {}) == '/tmp/a'


def test_extract_file_path_none_when_missing():
    assert hook_plugin.extract_file_path({}, {}) is None


def test_extract_file_path_handles_none_inputs():
    # Hook scripts sometimes call this with the raw payload which may be None.
    assert hook_plugin.extract_file_path({}, {}) is None


# --- find_latest_plan ----------------------------------------------------

def test_find_latest_plan_picks_newest(tmp_path):
    older = tmp_path / 'older.md'
    newer = tmp_path / 'newer.md'
    older.write_text('x')
    newer.write_text('y')
    # Force older mtime.
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    # Non-md file must be ignored even if newer.
    (tmp_path / 'skip.txt').write_text('ignored')
    os.utime(tmp_path / 'skip.txt', (9999, 9999))

    assert hook_plugin.find_latest_plan(str(tmp_path)) == 'newer.md'


def test_find_latest_plan_returns_none_when_dir_missing(tmp_path):
    assert hook_plugin.find_latest_plan(str(tmp_path / 'nope')) is None


def test_find_latest_plan_returns_none_when_no_md(tmp_path):
    (tmp_path / 'readme.txt').write_text('x')
    assert hook_plugin.find_latest_plan(str(tmp_path)) is None


# --- build_tool_attributes ----------------------------------------------

def test_build_tool_attributes_for_bash():
    attrs = hook_plugin.build_tool_attributes('Bash', {'command': 'ls -la'})
    assert attrs['tool_name'] == 'Bash'
    assert attrs['command_preview'] == 'ls -la'


def test_build_tool_attributes_truncates_long_bash_command():
    long = 'x' * 500
    attrs = hook_plugin.build_tool_attributes('Bash', {'command': long})
    assert len(attrs['command_preview']) == 200


def test_build_tool_attributes_for_mcp_tool():
    attrs = hook_plugin.build_tool_attributes('mcp__foo__bar', {'a': 1})
    assert attrs['mcp_tool'] == 'mcp__foo__bar'


def test_build_tool_attributes_for_skill():
    attrs = hook_plugin.build_tool_attributes('Skill', {'skill': 'my-skill'})
    assert attrs['skill_name'] == 'my-skill'


# --- post_event: retry / backoff behaviour ------------------------------
# These tests pin the contract introduced when `post_event` grew from a
# single-shot fire-and-forget into a bounded retry loop. They use stubs
# so no real HTTP, no real time.sleep happens.

import io
import json as _json
import urllib.error


class _UrlopenStub:
    """Stub for `urllib.request.urlopen`. Returns queued outcomes in order.

    Each item in `outcomes` is either an exception to raise or a real
    response-like object whose `.read()` returns b''.
    """

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append({'url': req.full_url, 'timeout': timeout,
                           'body': req.data})
        if not self.outcomes:
            raise AssertionError('urlopen called more times than outcomes queued')
        nxt = self.outcomes.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


def _ok_response():
    r = io.BytesIO(b'')
    return r


def _install_stubs(monkeypatch, outcomes, tmp_path):
    """Install urlopen stub + point the error log at tmp_path, and make
    time.sleep a no-op so the test suite doesn't wait real wall-clock."""
    stub = _UrlopenStub(outcomes)
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen', stub)
    monkeypatch.setattr(hook_plugin.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG',
                        str(tmp_path / 'ingest-errors.jsonl'))
    return stub


def _read_error_log(tmp_path):
    p = tmp_path / 'ingest-errors.jsonl'
    if not p.exists():
        return []
    return [_json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_post_event_succeeds_on_first_try(monkeypatch, tmp_path):
    stub = _install_stubs(monkeypatch, [_ok_response()], tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert len(stub.calls) == 1
    assert _read_error_log(tmp_path) == []


def test_post_event_retries_then_succeeds(monkeypatch, tmp_path):
    """Two URLError, then an OK — should do exactly 3 attempts and no
    give-up entry in the log."""
    outcomes = [
        urllib.error.URLError('connection refused'),
        urllib.error.URLError('connection refused'),
        _ok_response(),
    ]
    stub = _install_stubs(monkeypatch, outcomes, tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert len(stub.calls) == 3
    log = _read_error_log(tmp_path)
    # Two failures were logged, neither with gave_up=True.
    assert len(log) == 2
    assert [e['attempt'] for e in log] == [1, 2]
    assert all(e['gave_up'] is False for e in log)


def test_post_event_gives_up_after_max_attempts(monkeypatch, tmp_path):
    outcomes = [urllib.error.URLError('down')] * 5  # more than needed
    stub = _install_stubs(monkeypatch, outcomes, tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    # Default max_attempts=3.
    assert len(stub.calls) == 3
    log = _read_error_log(tmp_path)
    assert len(log) == 3
    assert [e['gave_up'] for e in log] == [False, False, True]


def test_post_event_does_not_retry_on_client_4xx(monkeypatch, tmp_path):
    """A 400 means the server won't accept this payload — retry is wasteful."""
    http_err = urllib.error.HTTPError(
        'http://x', 400, 'Bad Request', {}, io.BytesIO(b'bad'))
    stub = _install_stubs(monkeypatch, [http_err], tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert len(stub.calls) == 1
    log = _read_error_log(tmp_path)
    assert len(log) == 1
    assert log[0]['gave_up'] is True
    assert log[0]['http_status'] == 400


def test_post_event_retries_on_server_5xx(monkeypatch, tmp_path):
    outcomes = [
        urllib.error.HTTPError('http://x', 503, 'Unavailable', {},
                               io.BytesIO(b'')),
        _ok_response(),
    ]
    stub = _install_stubs(monkeypatch, outcomes, tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert len(stub.calls) == 2
    log = _read_error_log(tmp_path)
    assert len(log) == 1
    assert log[0]['http_status'] == 503
    assert log[0]['gave_up'] is False


def test_post_event_respects_env_retry_count(monkeypatch, tmp_path):
    monkeypatch.setenv('REGIN_INGEST_RETRIES', '1')
    outcomes = [urllib.error.URLError('down')] * 5
    stub = _install_stubs(monkeypatch, outcomes, tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert len(stub.calls) == 1
    log = _read_error_log(tmp_path)
    assert len(log) == 1
    assert log[0]['gave_up'] is True


def test_post_event_respects_env_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv('REGIN_INGEST_TIMEOUT_MS', '250')
    stub = _install_stubs(monkeypatch, [_ok_response()], tmp_path)
    hook_plugin.post_event('session_spans', {'trace_id': 't1'})
    assert stub.calls[0]['timeout'] == 0.25


def test_post_event_logs_give_up_when_no_url_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_plugin, 'DEFAULT_URLS', {})  # wipe defaults
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG',
                        str(tmp_path / 'ingest-errors.jsonl'))
    hook_plugin.post_event('unknown_endpoint', {'trace_id': 't1'})
    log = _read_error_log(tmp_path)
    assert len(log) == 1
    assert log[0]['gave_up'] is True
    assert log[0]['error_type'] == 'ValueError'


# --- ingest-error log rotation -----------------------------------------
# _log_ingest_error rotates the log once it hits the size cap. This
# keeps a sustained outage from filling the disk while still preserving
# the most recent history via a single `.1` backup.

def _force_log_to_size(path, target_bytes):
    """Write `target_bytes` of filler into `path` without overshooting."""
    with open(path, 'wb') as f:
        f.write(b'x' * target_bytes)


def test_log_does_not_rotate_below_threshold(monkeypatch, tmp_path):
    log = tmp_path / 'ingest-errors.jsonl'
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG', str(log))
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '1024')
    _force_log_to_size(log, 200)  # well under 1 KB

    hook_plugin._log_ingest_error('session_spans', 'http://x',
                                  ValueError('boom'))
    assert not (tmp_path / 'ingest-errors.jsonl.1').exists(), \
        'Below-threshold writes must not create a rotation backup.'
    # File still has the filler + one new JSONL line.
    assert log.stat().st_size > 200


def test_log_rotates_once_threshold_reached(monkeypatch, tmp_path):
    log = tmp_path / 'ingest-errors.jsonl'
    backup = tmp_path / 'ingest-errors.jsonl.1'
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG', str(log))
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '1024')
    _force_log_to_size(log, 2048)  # > 1 KB → should trigger rotation

    hook_plugin._log_ingest_error('session_spans', 'http://x',
                                  ValueError('post-rotation'))

    assert backup.exists(), 'Rotation must move the old log to `.1`.'
    # Fresh log holds only the post-rotation entry.
    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry['error'] == 'post-rotation'
    # Backup preserves the oversized original.
    assert backup.stat().st_size == 2048


def test_log_keeps_only_one_backup_across_rotations(monkeypatch, tmp_path):
    """Second rotation overwrites the first backup. Bounded disk use:
    at most 2 × max_bytes lives on disk at any time."""
    log = tmp_path / 'ingest-errors.jsonl'
    backup = tmp_path / 'ingest-errors.jsonl.1'
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG', str(log))
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '1024')

    # First rotation.
    _force_log_to_size(log, 2048)
    hook_plugin._log_ingest_error('session_spans', 'http://x',
                                  ValueError('first-rotation'))
    assert backup.exists()
    first_backup_size = backup.stat().st_size

    # Second rotation — backup should be replaced by the more recent
    # pre-rotation log contents, not have both piled together.
    _force_log_to_size(log, 4096)
    hook_plugin._log_ingest_error('session_spans', 'http://x',
                                  ValueError('second-rotation'))
    assert backup.stat().st_size == 4096
    assert backup.stat().st_size != first_backup_size


def test_log_survives_rotate_os_error(monkeypatch, tmp_path):
    """If os.replace raises (e.g. across filesystems in some exotic
    setup), _log_ingest_error must not propagate. The write still
    attempts to go through."""
    log = tmp_path / 'ingest-errors.jsonl'
    monkeypatch.setattr(hook_plugin, '_INGEST_ERROR_LOG', str(log))
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '10')
    _force_log_to_size(log, 100)

    def _boom(*a, **kw):
        raise OSError('cross-device link')
    monkeypatch.setattr(hook_plugin.os, 'replace', _boom)

    # Should not raise.
    hook_plugin._log_ingest_error('session_spans', 'http://x',
                                  ValueError('still-logged'))
    # And the entry still landed on the existing file.
    assert 'still-logged' in log.read_text()


def test_log_max_bytes_respects_env_floor(monkeypatch):
    """Setting the cap to an absurdly small value must be clamped to
    the 1 KB floor so a mis-configured env doesn't make every write
    cause a rotation."""
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '10')
    assert hook_plugin._ingest_log_max_bytes() == \
        hook_plugin._INGEST_LOG_MAX_BYTES_DEFAULT


def test_log_max_bytes_uses_env_when_valid(monkeypatch):
    monkeypatch.setenv('REGIN_INGEST_LOG_MAX_BYTES', '4096')
    assert hook_plugin._ingest_log_max_bytes() == 4096


# --- _jittered_backoff_ms ---------------------------------------------
# The retry loop jitters each sleep ±50% so a burst of simultaneous
# hook failures doesn't produce a synchronised retry wave. These tests
# pin that contract: range is bounded, zero-base stays zero, and with a
# seed the sequence is deterministic (for test replay + on-call field
# repro), while within one seed the sequence advances across calls (so
# back-to-back retries don't keep hitting the exact same instant).

def test_jitter_returns_zero_when_base_is_zero(monkeypatch):
    monkeypatch.delenv('REGIN_INGEST_BACKOFF_JITTER_SEED', raising=False)
    assert hook_plugin._jittered_backoff_ms(0) == 0


def test_jitter_stays_within_band(monkeypatch):
    """Over a thousand samples with a fixed base, every result must
    fall inside ±50% of base. Without this, the 'bounded backoff total
    latency' guarantee from round 3 would be broken."""
    monkeypatch.delenv('REGIN_INGEST_BACKOFF_JITTER_SEED', raising=False)
    base = 200
    samples = [hook_plugin._jittered_backoff_ms(base) for _ in range(1000)]
    assert min(samples) >= int(base * 0.5)
    assert max(samples) <= int(base * 1.5)
    # Mean should be roughly `base` (within 5% over 1 000 samples).
    mean = sum(samples) / len(samples)
    assert abs(mean - base) < base * 0.05


def test_jitter_is_deterministic_with_seed(monkeypatch):
    """Same seed → same sequence across process runs. Lets operators
    replay a specific retry cadence reported from the field."""
    hook_plugin._reset_jitter_rngs()
    monkeypatch.setenv('REGIN_INGEST_BACKOFF_JITTER_SEED', 'alpha')
    seq_a = [hook_plugin._jittered_backoff_ms(100) for _ in range(5)]

    hook_plugin._reset_jitter_rngs()
    monkeypatch.setenv('REGIN_INGEST_BACKOFF_JITTER_SEED', 'alpha')
    seq_b = [hook_plugin._jittered_backoff_ms(100) for _ in range(5)]

    assert seq_a == seq_b


def test_jitter_sequence_advances_across_calls(monkeypatch):
    """With a seed, successive calls must NOT return the same value —
    otherwise a 3-retry loop would fire at 3 identical instants, which
    is the exact thundering-herd scenario jitter is meant to break."""
    hook_plugin._reset_jitter_rngs()
    monkeypatch.setenv('REGIN_INGEST_BACKOFF_JITTER_SEED', 'beta')
    seq = [hook_plugin._jittered_backoff_ms(1000) for _ in range(10)]
    # Not all identical.
    assert len(set(seq)) > 1


def test_jitter_different_seeds_produce_different_sequences(monkeypatch):
    hook_plugin._reset_jitter_rngs()
    monkeypatch.setenv('REGIN_INGEST_BACKOFF_JITTER_SEED', 'gamma')
    seq_gamma = [hook_plugin._jittered_backoff_ms(100) for _ in range(5)]
    hook_plugin._reset_jitter_rngs()
    monkeypatch.setenv('REGIN_INGEST_BACKOFF_JITTER_SEED', 'delta')
    seq_delta = [hook_plugin._jittered_backoff_ms(100) for _ in range(5)]
    assert seq_gamma != seq_delta


# --- build_span ---------------------------------------------------------
# build_span shapes every span before POSTing. The REGIN_TRACE_TEST env var
# lets an integration-test harness stamp `is_test=true` on every span it
# produces so the Trace UI can hide them from the default view. These tests
# pin both the basic shape and the test-stamping contract.

def test_build_span_has_required_shape():
    span = hook_plugin.build_span(trace_id='t1', name='tool.Bash')
    required = {
        'trace_id', 'span_id', 'parent_id', 'name', 'kind',
        'start_time', 'end_time', 'duration_ms', 'attributes', 'status_code',
    }
    assert required <= set(span.keys())
    assert span['trace_id'] == 't1'
    assert span['name'] == 'tool.Bash'
    assert span['kind'] == 'internal'
    assert span['status_code'] == 'OK'
    assert isinstance(span['span_id'], str) and len(span['span_id']) == 16


def test_build_span_attributes_default_to_empty_dict(monkeypatch):
    monkeypatch.delenv('REGIN_TRACE_TEST', raising=False)
    span = hook_plugin.build_span(trace_id='t', name='n')
    assert span['attributes'] == {}


def test_build_span_attributes_copy_is_independent_of_caller(monkeypatch):
    """Mutating the attributes dict after build_span must not mutate the
    built span (and vice versa). build_span internally does `dict(attributes)`
    for this reason — without the copy, a caller reusing one dict across
    many spans would end up with every span pointing at the same dict."""
    src = {'key': 'v1'}
    span = hook_plugin.build_span(trace_id='t', name='n', attributes=src)
    src['key'] = 'mutated'
    assert span['attributes']['key'] == 'v1'


def test_build_span_stamps_is_test_when_env_set(monkeypatch):
    """REGIN_TRACE_TEST=1 → every span carries is_test=True. The Trace UI
    uses this to hide E2E test sessions from the default view."""
    monkeypatch.setenv('REGIN_TRACE_TEST', '1')
    span = hook_plugin.build_span(trace_id='t', name='tool.Read')
    assert span['attributes']['is_test'] is True


def test_build_span_does_not_stamp_is_test_when_env_falsy(monkeypatch):
    """Only '1'/'true'/'yes' (case-insensitive) are truthy. Other values
    — '0', empty, 'maybe' — must NOT stamp is_test, otherwise the Trace
    UI would hide real prod sessions."""
    for val in ('', '0', 'false', 'no', 'maybe'):
        monkeypatch.setenv('REGIN_TRACE_TEST', val)
        span = hook_plugin.build_span(trace_id='t', name='n')
        assert 'is_test' not in span['attributes'], \
            f'REGIN_TRACE_TEST={val!r} incorrectly stamped is_test=True'


def test_build_span_stamps_test_name_alongside_is_test(monkeypatch):
    """If REGIN_TRACE_TEST_NAME is also set (pytest nodeid), it joins the
    span as `test_name`. The UI uses this to label test runs when the
    user opts into viewing them."""
    monkeypatch.setenv('REGIN_TRACE_TEST', 'true')
    monkeypatch.setenv('REGIN_TRACE_TEST_NAME', 'tests/test_foo.py::test_bar')
    span = hook_plugin.build_span(trace_id='t', name='n')
    assert span['attributes']['test_name'] == 'tests/test_foo.py::test_bar'


def test_build_span_uses_supplied_span_id():
    """Allowing the caller to pin span_id is load-bearing: the runner
    sometimes needs to POST a span that references a parent_id the model
    already knows about."""
    span = hook_plugin.build_span(trace_id='t', name='n', span_id='abcdef0123456789')
    assert span['span_id'] == 'abcdef0123456789'


def test_build_span_uses_supplied_times_and_duration():
    span = hook_plugin.build_span(
        trace_id='t', name='n',
        start_time='2026-04-20T09:00:00',
        end_time='2026-04-20T09:00:01',
        duration_ms=1000,
        status_code='ERROR',
    )
    assert span['start_time'] == '2026-04-20T09:00:00'
    assert span['end_time'] == '2026-04-20T09:00:01'
    assert span['duration_ms'] == 1000
    assert span['status_code'] == 'ERROR'


# --- emit_response ------------------------------------------------------

def test_emit_response_writes_json_to_stdout(capsys):
    hook_plugin.emit_response('PreToolUse', 'ctx text')
    out = capsys.readouterr().out
    obj = json.loads(out.strip())
    assert obj['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    assert obj['hookSpecificOutput']['additionalContext'] == 'ctx text'
    assert obj['suppressOutput'] is True


def test_emit_response_suppress_false_respected(capsys):
    hook_plugin.emit_response('Stop', 'not silent', suppress_output=False)
    obj = json.loads(capsys.readouterr().out.strip())
    assert obj['suppressOutput'] is False


# --- module-level post_span wraps post_event ----------------------------

def test_module_post_span_calls_post_event(monkeypatch):
    """post_span → post_event('session_spans', <built span>). Pinning
    this indirection prevents a refactor from accidentally skipping the
    `session_spans` endpoint and shipping every span to `skill_reads`."""
    monkeypatch.delenv('REGIN_TRACE_TEST', raising=False)
    calls: list[tuple] = []
    monkeypatch.setattr(hook_plugin, 'post_event',
                        lambda endpoint, data: calls.append((endpoint, data)))
    hook_plugin.post_span(trace_id='s1', name='tool.Bash',
                          attributes={'cmd': 'pwd'})
    assert len(calls) == 1
    endpoint, data = calls[0]
    assert endpoint == 'session_spans'
    assert data['trace_id'] == 's1'
    assert data['name'] == 'tool.Bash'
    assert data['attributes'] == {'cmd': 'pwd'}


def test_module_post_span_noop_when_trace_id_missing(monkeypatch):
    """No trace_id → no POST. Otherwise we'd ingest orphan spans that
    can never be grafted under a session."""
    calls: list[tuple] = []
    monkeypatch.setattr(hook_plugin, 'post_event',
                        lambda endpoint, data: calls.append((endpoint, data)))
    hook_plugin.post_span(trace_id=None, name='x')
    hook_plugin.post_span(trace_id='', name='x')
    assert calls == []


# --- _env_int ----------------------------------------------------------

def test_env_int_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv('NON_EXISTENT_VAR', raising=False)
    assert hook_plugin._env_int('NON_EXISTENT_VAR', default=17) == 17


def test_env_int_returns_default_when_empty(monkeypatch):
    monkeypatch.setenv('MY_VAR', '')
    assert hook_plugin._env_int('MY_VAR', default=42) == 42


def test_env_int_returns_default_on_non_numeric(monkeypatch):
    """Non-integer values (typos, comments left in) fall back instead of
    crashing the hook on startup."""
    monkeypatch.setenv('MY_VAR', 'not_a_number')
    assert hook_plugin._env_int('MY_VAR', default=5) == 5


def test_env_int_clamps_below_lo(monkeypatch):
    monkeypatch.setenv('MY_VAR', '-1000')
    assert hook_plugin._env_int('MY_VAR', default=10, lo=50, hi=100) == 50


def test_env_int_clamps_above_hi(monkeypatch):
    """Prevents a mis-set REGIN_INGEST_TIMEOUT_MS=999999999 from turning
    every hook into a 15-minute-timeout stall."""
    monkeypatch.setenv('MY_VAR', '999999999')
    assert hook_plugin._env_int('MY_VAR', default=10, lo=50, hi=100) == 100


def test_env_int_passes_through_valid_value(monkeypatch):
    monkeypatch.setenv('MY_VAR', '250')
    assert hook_plugin._env_int('MY_VAR', default=500) == 250


# --- _is_retryable -----------------------------------------------------

def test_is_retryable_http_4xx_is_not_retryable():
    """4xx means the server rejected this payload; retrying won't help."""
    err = urllib.error.HTTPError('http://x', 400, 'Bad Request', {},
                                 io.BytesIO(b''))
    assert hook_plugin._is_retryable(err) is False


def test_is_retryable_http_401_is_not_retryable():
    err = urllib.error.HTTPError('http://x', 401, 'Unauthorized', {},
                                 io.BytesIO(b''))
    assert hook_plugin._is_retryable(err) is False


def test_is_retryable_http_5xx_is_retryable():
    """5xx is transient — retry during a server restart or overload."""
    err = urllib.error.HTTPError('http://x', 503, 'Unavailable', {},
                                 io.BytesIO(b''))
    assert hook_plugin._is_retryable(err) is True


def test_is_retryable_http_500_is_retryable():
    err = urllib.error.HTTPError('http://x', 500, 'Server Error', {},
                                 io.BytesIO(b''))
    assert hook_plugin._is_retryable(err) is True


def test_is_retryable_url_error():
    """Connection refused / DNS failure etc. is retryable."""
    assert hook_plugin._is_retryable(urllib.error.URLError('down')) is True


def test_is_retryable_oserror_retryable():
    """Connection timeout / socket errors surface as OSError — retry."""
    assert hook_plugin._is_retryable(OSError('timeout')) is True


def test_is_retryable_value_error_is_not_retryable():
    """A payload serialization error won't un-break itself on retry."""
    assert hook_plugin._is_retryable(ValueError('bad payload')) is False


# --- _get_url ----------------------------------------------------------

def test_get_url_returns_default_for_known_endpoint(monkeypatch):
    """No env override → use DEFAULT_URLS."""
    for key in ('session_spans', 'skill_reads', 'plan_sessions', 'rule_triggers'):
        monkeypatch.delenv(f'REGIN_{key.upper()}_TRACE_URL', raising=False)
    monkeypatch.delenv('REGIN_RULE_TRACE_URL', raising=False)
    assert hook_plugin._get_url('session_spans') == \
        hook_plugin.DEFAULT_URLS['session_spans']


def test_get_url_respects_env_override(monkeypatch):
    """Operators can redirect ingest to a staging host via env."""
    monkeypatch.setenv('REGIN_SESSION_SPANS_TRACE_URL', 'http://staging/api/spans')
    assert hook_plugin._get_url('session_spans') == 'http://staging/api/spans'


def test_get_url_prefers_new_env_over_legacy(monkeypatch):
    """For rule_triggers, REGIN_RULE_TRIGGERS_TRACE_URL (new) must win
    over REGIN_RULE_TRACE_URL (legacy) when both are set — otherwise
    migrations are stuck because the new env never takes effect."""
    monkeypatch.setenv('REGIN_RULE_TRIGGERS_TRACE_URL', 'http://new/rules')
    monkeypatch.setenv('REGIN_RULE_TRACE_URL', 'http://legacy/rules')
    assert hook_plugin._get_url('rule_triggers') == 'http://new/rules'


def test_get_url_falls_back_to_legacy_when_new_absent(monkeypatch):
    """Legacy env still works for users who haven't migrated."""
    monkeypatch.delenv('REGIN_RULE_TRIGGERS_TRACE_URL', raising=False)
    monkeypatch.setenv('REGIN_RULE_TRACE_URL', 'http://legacy/rules')
    assert hook_plugin._get_url('rule_triggers') == 'http://legacy/rules'


def test_get_url_legacy_fallback_only_for_rule_triggers(monkeypatch):
    """The legacy fallback is scoped to rule_triggers — setting
    REGIN_SESSION_SPANS_LEGACY_URL or similar must NOT resolve for
    other endpoints."""
    monkeypatch.delenv('REGIN_SESSION_SPANS_TRACE_URL', raising=False)
    # Setting a look-alike env that isn't recognized.
    monkeypatch.setenv('REGIN_SESSION_LEGACY_URL', 'http://nope')
    assert hook_plugin._get_url('session_spans') == \
        hook_plugin.DEFAULT_URLS['session_spans']


def test_get_url_returns_none_for_unknown_endpoint(monkeypatch):
    """An endpoint not in DEFAULT_URLS and not env-overridden → None.
    post_event uses this to emit a gave_up log entry instead of
    crashing on a missing key."""
    assert hook_plugin._get_url('no_such_endpoint') is None


# --- _post_once -------------------------------------------------------

def test_post_once_success(monkeypatch):
    """Happy path: urlopen returns cleanly → (True, None). No retry
    logic exercised at this level, but the wrapper's (ok, exc) contract
    is what post_event's retry loop keys off."""
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen',
                        lambda req, timeout: _ok_response())
    ok, exc = hook_plugin._post_once('http://x', b'{}', 1.0)
    assert ok is True and exc is None


def test_post_once_catches_urlerror(monkeypatch):
    """URLError → (False, exc) — never raised. Retry loop relies on
    this so one dead DNS doesn't crash the whole hook process."""
    boom = urllib.error.URLError('connection refused')
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen',
                        lambda req, timeout: (_ for _ in ()).throw(boom))
    ok, exc = hook_plugin._post_once('http://x', b'{}', 1.0)
    assert ok is False and exc is boom


def test_post_once_catches_httperror(monkeypatch):
    boom = urllib.error.HTTPError('http://x', 500, 'Server Error', {},
                                  io.BytesIO(b''))
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen',
                        lambda req, timeout: (_ for _ in ()).throw(boom))
    ok, exc = hook_plugin._post_once('http://x', b'{}', 1.0)
    assert ok is False and exc is boom


def test_post_once_catches_oserror(monkeypatch):
    """socket.timeout / ConnectionResetError / etc. surface as OSError.
    Caught at the source so retry logic can decide whether to redo."""
    boom = OSError('timeout')
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen',
                        lambda req, timeout: (_ for _ in ()).throw(boom))
    ok, exc = hook_plugin._post_once('http://x', b'{}', 1.0)
    assert ok is False and exc is boom


def test_post_once_catches_value_error(monkeypatch):
    """A malformed URL raises ValueError from Request() construction
    itself (before urlopen is called). The except tuple includes
    ValueError so the wrapper catches that too, rather than letting
    it leak up and crash the hook."""
    ok, exc = hook_plugin._post_once('not a url', b'{}', 1.0)
    assert ok is False
    assert isinstance(exc, ValueError)


def test_post_once_passes_timeout_through(monkeypatch):
    """The caller-supplied timeout must reach urlopen(timeout=...)
    verbatim. Any rounding or environment-based override here would
    break the _env_int → post_event → _post_once contract."""
    seen: dict = {}
    def _fake(req, timeout):
        seen['timeout'] = timeout
        return _ok_response()
    monkeypatch.setattr(hook_plugin.urllib.request, 'urlopen', _fake)
    hook_plugin._post_once('http://x', b'{}', 0.75)
    assert seen['timeout'] == 0.75
