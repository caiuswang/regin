"""Tests for individual handlers."""

import json
import os

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import commit_guard, session_lifecycle, skill_invoke, skill_read, trace_payload
from lib.providers import get_active_provider


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


# ── commit_guard (factory pattern) ────────────────────────────────────

def test_commit_guard_empty_repos_is_no_op():
    handle = commit_guard.make_handler([])
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m foo'}))
    assert r is None


def test_commit_guard_ignores_non_git_commit(tmp_path):
    # Even with repos configured, non-commit bash is untouched.
    guards = [commit_guard.GuardedRepo(str(tmp_path), 'main', '/does/not/exist')]
    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git status'}))
    assert r is None


def test_commit_guard_blocks_when_check_fails(monkeypatch, tmp_path):
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    check = tmp_path / 'check-staged.sh'
    check.write_text('#!/bin/bash\necho "violation in X.java"\nexit 1\n')
    check.chmod(0o755)

    guards = [commit_guard.GuardedRepo(str(repo), 'main', str(check))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m x'}))
    assert r and r.decision == 'block'
    assert 'violation in X.java' in r.decision_reason


def test_commit_guard_skips_when_branch_doesnt_match(monkeypatch, tmp_path):
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    check = tmp_path / 'check-staged.sh'
    check.write_text('#!/bin/bash\nexit 1\n')
    check.chmod(0o755)

    guards = [commit_guard.GuardedRepo(str(repo), 'feature/wanted', str(check))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m x'}))
    assert r is None


def test_commit_guard_factory_produces_independent_closures(tmp_path):
    """Two factories yield two handlers with different repo lists."""
    guards1 = [commit_guard.GuardedRepo(str(tmp_path / 'a'), 'main', 'a.sh')]
    guards2 = [commit_guard.GuardedRepo(str(tmp_path / 'b'), 'main', 'b.sh')]
    h1 = commit_guard.make_handler(guards1)
    h2 = commit_guard.make_handler(guards2)
    assert h1 is not h2


def test_commit_guard_allows_when_check_passes(monkeypatch, tmp_path):
    """rc=0 from the check script must not block. The existing suite
    covers the failure path (decision='block') but never locked in the
    happy path — a refactor that accidentally inverted the rc check
    would still pass every existing test."""
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    check = tmp_path / 'check-staged.sh'
    check.write_text('#!/bin/bash\nexit 0\n')
    check.chmod(0o755)

    guards = [commit_guard.GuardedRepo(str(repo), 'main', str(check))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m ok'}))
    assert r is None


def test_commit_guard_aggregates_messages_across_repos(monkeypatch, tmp_path):
    """When two guards both match and both fail, the handler must return
    a single block response whose reason concatenates both failure
    messages. Without this test, a change that short-circuits on the
    first failure would silently drop the second repo's output."""
    repo_a = tmp_path / 'repo-a'
    repo_a.mkdir()
    (repo_a / '.git').mkdir()
    repo_b = tmp_path / 'repo-b'
    repo_b.mkdir()
    (repo_b / '.git').mkdir()

    check_a = tmp_path / 'check-a.sh'
    check_a.write_text('#!/bin/bash\necho "A failed"\nexit 1\n')
    check_a.chmod(0o755)
    check_b = tmp_path / 'check-b.sh'
    check_b.write_text('#!/bin/bash\necho "B failed"\nexit 2\n')
    check_b.chmod(0o755)

    guards = [
        commit_guard.GuardedRepo(str(repo_a), 'main', str(check_a)),
        commit_guard.GuardedRepo(str(repo_b), 'main', str(check_b)),
    ]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m both'}))
    assert r and r.decision == 'block'
    assert 'A failed' in r.decision_reason
    assert 'B failed' in r.decision_reason
    # Each repo contributes a labelled section — basename is the label.
    assert 'repo-a' in r.decision_reason
    assert 'repo-b' in r.decision_reason


def test_commit_guard_one_pass_one_fail_still_blocks(monkeypatch, tmp_path):
    """Even when one guard passes, a single failing guard must block —
    and the passing guard's label must NOT appear in the reason."""
    repo_ok = tmp_path / 'repo-ok'
    repo_ok.mkdir()
    (repo_ok / '.git').mkdir()
    repo_bad = tmp_path / 'repo-bad'
    repo_bad.mkdir()
    (repo_bad / '.git').mkdir()

    check_ok = tmp_path / 'check-ok.sh'
    check_ok.write_text('#!/bin/bash\nexit 0\n')
    check_ok.chmod(0o755)
    check_bad = tmp_path / 'check-bad.sh'
    check_bad.write_text('#!/bin/bash\necho "bad repo failed"\nexit 1\n')
    check_bad.chmod(0o755)

    guards = [
        commit_guard.GuardedRepo(str(repo_ok), 'main', str(check_ok)),
        commit_guard.GuardedRepo(str(repo_bad), 'main', str(check_bad)),
    ]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m mixed'}))
    assert r and r.decision == 'block'
    assert 'bad repo failed' in r.decision_reason
    assert 'repo-bad' in r.decision_reason
    assert 'repo-ok' not in r.decision_reason


def test_commit_guard_skips_missing_repo_dir(monkeypatch, tmp_path):
    """If repo_dir no longer exists on disk (repo was moved/deleted),
    the handler must silently skip that guard — not raise and not crash
    the calling hook."""
    missing = tmp_path / 'never-existed'
    guards = [commit_guard.GuardedRepo(str(missing), 'main', '/never/matters.sh')]
    # Branch lookup should never be called since repo_dir is missing.
    monkeypatch.setattr(commit_guard, '_current_branch',
                        lambda d: (_ for _ in ()).throw(
                            AssertionError('branch lookup ran despite missing repo_dir')))

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m x'}))
    assert r is None


def test_commit_guard_skips_missing_check_script(monkeypatch, tmp_path):
    """Repo exists and branch matches, but the configured check_script
    is gone. Don't block — just skip that guard. A missing script means
    'no check', not 'fail closed'."""
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    guards = [commit_guard.GuardedRepo(str(repo), 'main', str(tmp_path / 'nope.sh'))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m x'}))
    assert r is None


def test_commit_guard_swallows_check_script_errors(monkeypatch, tmp_path):
    """If the check script can't run (timeout / FileNotFoundError),
    `_run_check` returns (0, msg) so the commit is not blocked. Failing
    closed here would turn every hiccup into a false positive that
    locks the user out of committing."""
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    check = tmp_path / 'check.sh'
    check.write_text('#!/bin/bash\nexit 7\n')
    check.chmod(0o755)
    guards = [commit_guard.GuardedRepo(str(repo), 'main', str(check))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')
    # Simulate the subprocess itself failing to spawn.
    monkeypatch.setattr(commit_guard, '_run_check',
                        lambda s, d: (0, '[commit_guard] skipped: boom'))

    handle = commit_guard.make_handler(guards)
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git commit -m x'}))
    assert r is None


def test_commit_guard_regex_respects_word_boundary(tmp_path, monkeypatch):
    """`git committee` must not trigger the guard — `commit(?:\\s|$)`
    ensures we don't match a prefix of a longer word. Without this,
    innocuous commands would enter the full guard evaluation loop."""
    repo = tmp_path / 'fake-repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    check = tmp_path / 'check.sh'
    check.write_text('#!/bin/bash\nexit 1\n')
    check.chmod(0o755)
    guards = [commit_guard.GuardedRepo(str(repo), 'main', str(check))]
    monkeypatch.setattr(commit_guard, '_current_branch', lambda d: 'main')

    handle = commit_guard.make_handler(guards)
    # `git committee` is not a real command but it exercises the regex's
    # trailing word-boundary: we must NOT treat it as `git commit`.
    r = handle(_p('PreToolUse', tool_name='Bash',
                  tool_input={'command': 'git committee -m x'}))
    assert r is None


# ── skill_read ────────────────────────────────────────────────────────

def test_skill_read_detects_content_md(monkeypatch):
    home = '/tmp/testhome'
    monkeypatch.setenv('HOME', home)
    # Also override the home lookup that the handler performs.
    import hook_manager.handlers.skill_read as sr
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)
    rel = get_active_provider().skill_content_relpath('my-skill')

    r = sr.handle(_p('PostToolUse', tool_name='Read',
                     tool_input={'file_path': f'{home}/{rel}'}))
    assert r and 'my-skill' in (r.additional_context or '')


def test_skill_read_rejects_spoofed_prefix(monkeypatch):
    home = '/tmp/testhome'
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)
    r = skill_read.handle(_p('PostToolUse', tool_name='Read',
                             tool_input={'file_path': f'{home}/Xclaude/skills/foo/content.md'}))
    # The regex `^\.claude/skills/…` is anchored and must NOT match a path
    # rooted at `Xclaude/skills/…`. Silent-trace policy: no additional_context
    # for non-matches — handler just returns None.
    assert r is None


def test_skill_read_skips_non_read_tools():
    r = skill_read.handle(_p('PostToolUse', tool_name='Edit',
                             tool_input={'file_path': '/anything'}))
    assert r is None


def test_skill_read_returns_suppress_and_specific_context(monkeypatch):
    """Pin the exact additional_context string and suppress_output flag.
    The web dashboard scrapes this text out of the session transcript
    to build its 'skill reads per session' metric — changing the prefix
    would break that dashboard silently."""
    home = '/tmp/testhome'
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)
    rel = get_active_provider().skill_content_relpath('my-skill')
    r = skill_read.handle(_p('PostToolUse', tool_name='Read',
        tool_input={'file_path': f'{home}/{rel}'}))
    assert r is not None
    assert r.suppress_output is True
    assert r.additional_context == 'skill-read-trace: logged read of my-skill'


def test_skill_read_skips_non_skill_paths(monkeypatch):
    """Reading a plan, a controller file, or anything not matching the
    `.claude/skills/<id>/content.md` shape must not emit trace context.
    Without this, every Read call would spam additionalContext."""
    home = '/tmp/testhome'
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)
    for path in (
        f'{home}/.claude/plans/my-plan.md',
        f'{home}/project/src/Main.java',
        f'{home}/.claude/skills/my-skill/README.md',      # not content.md
        f'{home}/.claude/skills/foo/bar/content.md',      # nested, two dirs
    ):
        r = skill_read.handle(_p('PostToolUse', tool_name='Read',
                                 tool_input={'file_path': path}))
        assert r is None, f'unexpectedly matched path {path!r}'


def test_skill_read_skips_empty_and_missing_file_path(monkeypatch):
    """An empty string or a missing key must be handled without crashing.
    Claude Code can omit file_path on some edge-case Read errors."""
    home = '/tmp/testhome'
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)
    assert skill_read.handle(_p('PostToolUse', tool_name='Read',
                                tool_input={'file_path': ''})) is None
    assert skill_read.handle(_p('PostToolUse', tool_name='Read',
                                tool_input={})) is None


def test_skill_read_swallows_emit_span_errors(monkeypatch):
    """If the trace ingest is unreachable, post_span will raise. The
    handler must still return its additional_context — swallowing the
    exception — so the hook response stays clean and the transcript
    still surfaces the skill read."""
    home = '/tmp/testhome'
    monkeypatch.setattr(os.path, 'expanduser', lambda p: home if p == '~' else p)

    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)
    monkeypatch.setattr(hp, 'post_event', _boom)
    rel = get_active_provider().skill_content_relpath('my-skill')

    r = skill_read.handle(_p('PostToolUse', tool_name='Read',
        tool_input={'file_path': f'{home}/{rel}'}))
    assert r is not None
    assert r.additional_context == 'skill-read-trace: logged read of my-skill'


# ── skill_invoke ──────────────────────────────────────────────────────

def test_skill_invoke_detects_command_name(captured_spans):
    r = skill_invoke.handle(_p('UserPromptExpansion',
                                command_name='grit-rules',
                                command_source='skill',
                                command_args='--list'))
    assert r and 'grit-rules' in (r.additional_context or '')
    assert r.suppress_output is True
    assert captured_spans[0]['name'] == 'skill.invoke'
    assert captured_spans[0]['attributes']['skill_id'] == 'grit-rules'
    assert captured_spans[0]['attributes']['command_args'] == '--list'


def test_skill_invoke_skips_non_user_prompt_expansion():
    r = skill_invoke.handle(_p('PostToolUse', tool_name='Read'))
    assert r is None


def test_skill_invoke_skips_missing_command_name():
    r = skill_invoke.handle(_p('UserPromptExpansion', command_source='skill'))
    assert r is None


def test_skill_invoke_uses_defaults_for_optional_fields(captured_spans):
    r = skill_invoke.handle(_p('UserPromptExpansion', command_name='my-skill'))
    assert r is not None
    attrs = captured_spans[0]['attributes']
    assert attrs['command_source'] == 'unknown'
    assert attrs['command_args'] == ''


def test_skill_invoke_swallows_emit_errors(monkeypatch):
    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)
    monkeypatch.setattr(hp, 'post_event', _boom)

    r = skill_invoke.handle(_p('UserPromptExpansion', command_name='my-skill'))
    assert r is not None
    assert r.additional_context == 'skill-invoke-trace: logged invocation of my-skill'


# ── session_lifecycle ────────────────────────────────────────────────

def test_session_start_uses_source(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1', source='resume'))
    assert captured_spans[0]['name'] == 'session.start'
    assert captured_spans[0]['attributes']['source'] == 'resume'


def test_session_end_uses_reason(captured_spans):
    session_lifecycle.handle_end(_p('SessionEnd', session_id='s1', reason='logout'))
    assert captured_spans[0]['name'] == 'session.end'
    assert captured_spans[0]['attributes']['reason'] == 'logout'


def test_session_start_defaults_to_startup_when_missing(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1'))
    assert captured_spans[0]['attributes']['source'] == 'startup'


def test_session_handlers_return_suppress_output_no_context(captured_spans):
    """Silent-trace policy: session start/end post spans but add nothing
    to the transcript — the model doesn't need a breadcrumb when a
    session begins or ends."""
    for fn, ev in [
        (session_lifecycle.handle_start, 'SessionStart'),
        (session_lifecycle.handle_end, 'SessionEnd'),
    ]:
        r = fn(_p(ev, session_id='s1'))
        assert r is not None
        assert r.suppress_output is True
        assert r.additional_context is None


def test_session_start_captures_cwd(captured_spans):
    """When the payload carries cwd, it lands on the span — the trace
    dashboard groups sessions by repo via this attribute."""
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        cwd='/Users/me/projects/foo'))
    assert captured_spans[0]['attributes']['cwd'] == '/Users/me/projects/foo'


def test_session_end_without_reason_omits_attribute(captured_spans):
    """Unlike SessionStart which defaults to 'startup', SessionEnd has
    NO default for reason — if the payload lacks it, the attribute
    must be absent (not synthesized as empty/unknown, which would
    pollute dashboard filters)."""
    session_lifecycle.handle_end(_p('SessionEnd', session_id='s1'))
    attrs = captured_spans[0]['attributes']
    assert 'reason' not in attrs


def test_session_handlers_swallow_emit_errors(monkeypatch):
    """If the trace ingest is down, session boundary hooks must not
    crash Claude's startup/shutdown — even a best-effort span emission
    must swallow exceptions completely."""
    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r1 = session_lifecycle.handle_start(_p('SessionStart', session_id='s1'))
    r2 = session_lifecycle.handle_end(_p('SessionEnd', session_id='s1'))
    for r in (r1, r2):
        assert r is not None
        assert r.suppress_output is True


def test_session_stop_fallback_emits_for_codex_only(captured_spans, monkeypatch):
    monkeypatch.setattr(session_lifecycle,
                        '_session_agent_type_from_db', lambda _tid: 'codex')

    r = session_lifecycle.handle_stop_fallback(_p('Stop', session_id='s1'))
    assert r is not None and r.suppress_output is True
    assert captured_spans[0]['name'] == 'session.end'
    attrs = captured_spans[0]['attributes']
    assert attrs['reason'] == 'stop_fallback'
    assert attrs['synthetic'] is True
    assert attrs['source_event'] == 'Stop'


def test_session_stop_fallback_skips_non_codex(captured_spans, monkeypatch):
    monkeypatch.setattr(session_lifecycle,
                        '_session_agent_type_from_db', lambda _tid: 'claude')

    r = session_lifecycle.handle_stop_fallback(_p('Stop', session_id='s1'))
    assert r is None
    assert captured_spans == []


def test_session_stop_fallback_can_be_disabled_by_flag(captured_spans, monkeypatch):
    monkeypatch.setattr(session_lifecycle,
                        '_session_agent_type_from_db', lambda _tid: 'codex')
    monkeypatch.setenv('REGIN_CODEX_STOP_END_FALLBACK', '0')

    r = session_lifecycle.handle_stop_fallback(_p('Stop', session_id='s1'))
    assert r is None
    assert captured_spans == []


def test_session_stop_fallback_skips_unknown_session(captured_spans, monkeypatch):
    """If the session has no DB record (e.g. Stop arrived before SessionStart),
    the fallback is skipped rather than guessing from the global provider."""
    monkeypatch.setattr(session_lifecycle,
                        '_session_agent_type_from_db', lambda _tid: None)

    r = session_lifecycle.handle_stop_fallback(_p('Stop', session_id='s1'))
    assert r is None
    assert captured_spans == []


def test_session_stop_fallback_skips_claude_session_even_when_global_provider_is_codex(
    captured_spans, monkeypatch
):
    """When regin is configured as codex provider but the session is Claude,
    the Stop fallback must NOT fire — otherwise every per-turn Stop from
    Claude would incorrectly end the session."""
    monkeypatch.setattr(session_lifecycle,
                        '_session_agent_type_from_db', lambda _tid: 'claude')

    r = session_lifecycle.handle_stop_fallback(_p('Stop', session_id='s1'))
    assert r is None
    assert captured_spans == []


def test_session_start_captures_model(captured_spans):
    """SessionStart payload includes the model Claude is currently using
    (e.g. 'claude-haiku-4-5-20251001'). The handler stamps it on the
    session.start span so the Sessions dashboard can show which model
    ran each session — and `SELECT model, COUNT(*) ... GROUP BY model`
    gives per-model usage numbers."""
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        model='claude-haiku-4-5-20251001'))
    s = captured_spans[0]
    assert s['name'] == 'session.start'
    assert s['attributes']['model'] == 'claude-haiku-4-5-20251001'


def test_session_start_captures_provider_agent_type(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        agent_type='codex'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'codex'


def test_session_start_payload_agent_type_wins(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        agent_type='custom-agent'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'custom-agent'


def test_session_start_payload_agent_type_is_authoritative(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        agent_type='claude', model='gpt-5.5'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'claude'


def test_session_start_infers_codex_from_model_without_agent_type(captured_spans):
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        model='gpt-5.5'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'codex'


def test_session_start_omits_model_when_absent(captured_spans):
    """If the payload has no model field (older Claude Code versions
    or unusual session starts), the attribute must be absent — not
    stamped as empty string, which would pollute dashboard filters
    that match `model IS NOT NULL`."""
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1'))
    attrs = captured_spans[0]['attributes']
    assert 'model' not in attrs


def test_session_start_model_independent_of_source(captured_spans):
    """Model captured regardless of source (startup/resume/clear/compact).
    A resume on a different model (user had switched via /model before
    closing) still needs its model tracked — downstream aggregation
    counts sessions per model, not per source."""
    session_lifecycle.handle_start(_p('SessionStart', session_id='s1',
        source='resume', model='claude-opus-4-7'))
    attrs = captured_spans[0]['attributes']
    assert attrs['source'] == 'resume'
    assert attrs['model'] == 'claude-opus-4-7'


# ── trace_payload ─────────────────────────────────────────────────────

def test_trace_payload_writes_line_to_log(monkeypatch, tmp_path):
    log = tmp_path / 'hooks.jsonl'
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    r = trace_payload.handle(_p('Stop', session_id='abc'))
    assert r and r.suppress_output is True
    # No additional_context — this was the 2nd-biggest fix in the refactor.
    assert r.additional_context is None
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]['hook_event'] == 'Stop'


def test_trace_payload_rotates_when_oversized(monkeypatch, tmp_path):
    log = tmp_path / 'big.jsonl'
    log.write_text('x' * 1024)
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    monkeypatch.setattr(trace_payload, '_MAX_BYTES', 512)
    trace_payload.handle(_p('Stop'))
    assert (tmp_path / 'big.jsonl.1').exists(), 'rotation did not happen'
    # The new log file must contain exactly one line (the one we just wrote).
    assert len(log.read_text().splitlines()) == 1


def test_trace_payload_appends_across_calls(monkeypatch, tmp_path):
    """Three sequential Stop events must produce three lines. A
    regression where the handler truncated the file on each write
    (e.g. opening with 'w' instead of 'a') would kill the log."""
    log = tmp_path / 'hooks.jsonl'
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    for i in range(3):
        trace_payload.handle(_p('Stop', session_id=f's{i}'))
    lines = log.read_text().splitlines()
    assert len(lines) == 3
    sessions = [json.loads(ln)['session_id'] for ln in lines]
    assert sessions == ['s0', 's1', 's2']


def test_trace_payload_creates_parent_dir_if_missing(monkeypatch, tmp_path):
    """First-run scenario: ~/.claude/hook-payloads.jsonl's parent dir
    may not exist yet. The handler uses os.makedirs(exist_ok=True) so
    the first Stop event creates the tree instead of silently failing."""
    log = tmp_path / 'nested' / 'deeper' / 'payloads.jsonl'
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    trace_payload.handle(_p('Stop', session_id='first'))
    assert log.exists()
    assert json.loads(log.read_text().strip())['session_id'] == 'first'


def test_trace_payload_preserves_unicode(monkeypatch, tmp_path):
    """ensure_ascii=False keeps unicode chars intact in the jsonl.
    Without this, a prompt containing 日本語 or émoji would be written
    as \\u escape sequences — harder to grep, larger on disk."""
    log = tmp_path / 'uni.jsonl'
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    # The handler logs payload.raw; construct a raw payload with unicode.
    from hook_manager.core import HookPayload
    payload = HookPayload.from_stdin_json('Stop', {
        'hook_event_name': 'Stop', 'note': 'unicode: 日本語 / émoji 🌟',
    })
    trace_payload.handle(payload)
    raw = log.read_text()
    assert '日本語' in raw
    assert '🌟' in raw


def test_trace_payload_swallows_os_errors(monkeypatch, tmp_path):
    """Write path failures (disk full, permission issue) must not
    propagate — the handler's try/except OSError is explicit. Return
    still has to be a clean HookResponse so the pipeline finishes."""
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(tmp_path / 'hooks.jsonl'))
    real_open = open

    def bad_open(path, *a, **kw):
        if str(path).endswith('hooks.jsonl'):
            raise OSError('disk full')
        return real_open(path, *a, **kw)

    monkeypatch.setattr('builtins.open', bad_open)
    r = trace_payload.handle(_p('Stop', session_id='doomed'))
    assert r is not None
    assert r.suppress_output is True


def test_trace_payload_rotation_overwrites_existing_backup(monkeypatch, tmp_path):
    """After two rotations, only one `.1` backup exists — the second
    overwrites the first. Bounded disk: at most 2×_MAX_BYTES on disk
    regardless of how many rotations happen."""
    log = tmp_path / 'repeat.jsonl'
    backup = tmp_path / 'repeat.jsonl.1'
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(log))
    monkeypatch.setattr(trace_payload, '_MAX_BYTES', 100)

    log.write_text('a' * 200)
    trace_payload.handle(_p('Stop', session_id='first-rot'))
    first_backup_contents = backup.read_text()

    # Grow current log past threshold again.
    log.write_text(log.read_text() + 'b' * 200)
    trace_payload.handle(_p('Stop', session_id='second-rot'))

    # Backup replaced, not appended to.
    assert backup.read_text() != first_backup_contents
    assert not (tmp_path / 'repeat.jsonl.2').exists()




# ── skill_experience injection → trace span ───────────────────────────
# Both delivery paths must emit a `memory.recall` span (source=
# 'skill_experience') so the injected <skill_experience> block shows in the
# session trace detail. Regression for: injection happened but nothing showed.

import lib.memory.skill_experience as skill_exp_mod
from hook_manager.handlers import memory_recall
from hook_manager.handlers import skill_experience as skill_exp_handler
from lib.settings import settings as _settings

_FAKE_HITS = [{'id': 'abc12345', 'kind': 'lesson', 'title': 't',
               'scope': 'repo:regin'}]
_FAKE_BLOCK = '<skill_experience>\n- [lesson] t: body (memory abc)\n</skill_experience>'


def test_skill_experience_auto_invoke_emits_recall_span(captured_spans, monkeypatch):
    monkeypatch.setattr(skill_exp_mod, 'skill_experience_injection',
                        lambda *_a, **_k: (_FAKE_BLOCK, _FAKE_HITS))
    r = skill_exp_handler.handle(
        _p('PreToolUse', tool_name='Skill', session_id='sess-1',
           tool_input={'skill': 'topic-router'}))
    assert r and '<skill_experience>' in (r.additional_context or '')
    span = next(s for s in captured_spans if s['name'] == 'memory.recall')
    attrs = span['attributes']
    assert attrs['source'] == 'skill_experience'
    assert attrs['skill_id'] == 'topic-router'
    assert attrs['hit_count'] == 1
    assert attrs['block'] == _FAKE_BLOCK
    assert attrs['hits'][0]['id'] == 'abc12345'


def test_skill_experience_no_block_emits_no_span(captured_spans, monkeypatch):
    monkeypatch.setattr(skill_exp_mod, 'skill_experience_injection',
                        lambda *_a, **_k: ('', []))
    r = skill_exp_handler.handle(
        _p('PreToolUse', tool_name='Skill', tool_input={'skill': 'unknown'}))
    assert r is None
    assert not [s for s in captured_spans if s['name'] == 'memory.recall']


def test_skill_experience_skips_non_skill_tool():
    assert skill_exp_handler.handle(_p('PreToolUse', tool_name='Read')) is None


def test_skill_experience_inject_survives_span_failure(monkeypatch):
    """Tracing is best-effort: if span emission raises, the inject is still
    delivered (memory must never block a tool call)."""
    monkeypatch.setattr(skill_exp_mod, 'skill_experience_injection',
                        lambda *_a, **_k: (_FAKE_BLOCK, _FAKE_HITS))

    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r = skill_exp_handler.handle(
        _p('PreToolUse', tool_name='Skill', tool_input={'skill': 'topic-router'}))
    assert r and '<skill_experience>' in (r.additional_context or '')


def test_skill_experience_slash_command_emits_span(captured_spans, monkeypatch):
    """Bare `/skill` (no recall query) still traces its skill experience —
    the leg fires independently of `_eligible_prompt`."""
    monkeypatch.setattr(skill_exp_mod, 'skill_experience_injection',
                        lambda *_a, **_k: (_FAKE_BLOCK, _FAKE_HITS))
    out = memory_recall._skill_experience(
        _p('UserPromptSubmit', prompt='/playwright-screenshots',
           session_id='sess-1'),
        _settings.agent_memory)
    assert '<skill_experience>' in out
    span = next(s for s in captured_spans if s['name'] == 'memory.recall')
    assert span['attributes']['source'] == 'skill_experience'
    # The leading slash of the command token is stripped on the span.
    assert span['attributes']['skill_id'] == 'playwright-screenshots'


def test_emit_skill_experience_span_gated_by_trace_recall(captured_spans, monkeypatch):
    monkeypatch.setattr(_settings.agent_memory, 'trace_recall', False)
    skill_exp_mod.emit_skill_experience_span('sid', 'topic-router',
                                             _FAKE_BLOCK, _FAKE_HITS)
    assert not captured_spans
