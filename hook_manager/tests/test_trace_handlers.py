"""Tests for prompt_trace and post_tool_trace."""

import os

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace, prompt_trace


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


class _FakeProvider:
    def __init__(self, plans_dir):
        self._plans_dir = plans_dir

    def plans_dir(self):
        return self._plans_dir


def _payload_with_provider(event, plans_dir, **kw):
    payload = _p(event, **kw)
    payload.__dict__['resolved_provider'] = _FakeProvider(plans_dir)
    return payload


@pytest.fixture
def captured_spans(monkeypatch):
    """Stub lib.hook_plugin.post_span; return the list it collects into."""
    import lib.hook_plugin as hp
    spans: list[dict] = []

    def _capture(**kw):
        spans.append(kw)

    monkeypatch.setattr(hp, 'post_span', _capture)
    return spans


# ── prompt_trace ──────────────────────────────────────────────────────

def test_prompt_trace_skips_empty_prompt(captured_spans):
    r = prompt_trace.handle(_p('UserPromptSubmit', prompt=''))
    assert r is None
    assert captured_spans == []


def test_prompt_trace_posts_span_with_text(captured_spans):
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='hello world', session_id='s1'))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'prompt'
    assert s['trace_id'] == 's1'
    assert s['attributes']['text'] == 'hello world'
    assert s['attributes']['chars'] == 11


def test_prompt_trace_keeps_full_long_text(captured_spans):
    long = 'x' * 5000
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt=long, session_id='s1'))
    text = captured_spans[0]['attributes']['text']
    assert text == long
    assert captured_spans[0]['attributes']['chars'] == 5000


def test_prompt_trace_detects_slash_command(captured_spans):
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='/plan do a thing', session_id='s1'))
    assert captured_spans[0]['attributes']['slash_command'] == '/plan'


def test_prompt_trace_no_slash_command_for_regular_prompt(captured_spans):
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='hello world', session_id='s1'))
    assert captured_spans[0]['attributes']['slash_command'] is None


def test_prompt_trace_detects_plan_approval(captured_spans, tmp_path):
    plan = tmp_path / 'my-plan.md'
    plan.write_text('# Plan\n')
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path, prompt='looks good, proceed'))
    assert r and r.additional_context == 'plan_decision=approved'


def test_prompt_trace_detects_plan_rejection(captured_spans, tmp_path):
    plan = tmp_path / 'my-plan.md'
    plan.write_text('# Plan\n')
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path, prompt='no thanks, cancel'))
    assert r and r.additional_context == 'plan_decision=rejected'


def test_prompt_trace_no_plan_means_no_decision(captured_spans, tmp_path):
    # Empty plans dir → no "active plan" → response emitted but without plan_decision
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path, prompt='looks good'))
    assert r is not None
    assert r.additional_context is None


def test_prompt_trace_stale_plan_means_no_decision(captured_spans, tmp_path):
    """A plan modified > 1 hour ago is not 'active'."""
    plan = tmp_path / 'old.md'
    plan.write_text('x')
    os.utime(plan, (1000, 1000))  # 1970 mtime
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path, prompt='approve'))
    assert r is not None
    assert r.additional_context is None


def test_prompt_trace_every_approve_keyword_detected(captured_spans, tmp_path):
    """Each keyword in the approve table must actually trigger detection —
    catches regressions like accidentally typo-ing a keyword when editing
    the list (which would silently stop classifying those prompts)."""
    plan = tmp_path / 'p.md'; plan.write_text('x')
    for kw in prompt_trace._APPROVE_WORDS:
        r = prompt_trace.handle(_payload_with_provider(
            'UserPromptSubmit', tmp_path,
            prompt=f'I think we should {kw} with this change'))
        assert r and r.additional_context == 'plan_decision=approved', \
            f'approve keyword {kw!r} did not classify'


def test_prompt_trace_every_reject_keyword_detected(captured_spans, tmp_path):
    plan = tmp_path / 'p.md'; plan.write_text('x')
    for kw in prompt_trace._REJECT_WORDS:
        r = prompt_trace.handle(_payload_with_provider(
            'UserPromptSubmit', tmp_path,
            prompt=f'please {kw} that approach'))
        assert r and r.additional_context == 'plan_decision=rejected', \
            f'reject keyword {kw!r} did not classify'


def test_prompt_trace_detection_is_case_insensitive(captured_spans, tmp_path):
    """Users often shout APPROVE or type Proceed with caps. The lower()
    normalization inside _detect_decision must survive refactors."""
    plan = tmp_path / 'p.md'; plan.write_text('x')
    for variant in ('APPROVE', 'Approve', 'aPPRoVe'):
        r = prompt_trace.handle(_payload_with_provider(
            'UserPromptSubmit', tmp_path, prompt=variant))
        assert r and r.additional_context == 'plan_decision=approved', \
            f'case variant {variant!r} failed to classify'


def test_prompt_trace_approve_wins_over_reject_when_both_present(captured_spans, tmp_path):
    """The detector checks approve keywords first. If a prompt contains
    both (e.g. "approve this but cancel that other thing"), approved
    must win — reordering the loops would silently flip the outcome."""
    plan = tmp_path / 'p.md'; plan.write_text('x')
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path,
        prompt='approve the A but cancel the B'))
    assert r and r.additional_context == 'plan_decision=approved'


def test_prompt_trace_decision_none_when_no_keyword_matches(captured_spans, tmp_path):
    """A plain question during plan mode (no keyword) must NOT produce a
    plan_decision line — otherwise every follow-up prompt during plan
    mode would get spuriously labelled."""
    plan = tmp_path / 'p.md'; plan.write_text('x')
    r = prompt_trace.handle(_payload_with_provider(
        'UserPromptSubmit', tmp_path,
        prompt='what does this function do?'))
    assert r is not None
    assert r.additional_context is None


def test_plan_is_active_respects_fresh_seconds(tmp_path):
    """Direct unit test on _plan_is_active: custom fresh_seconds window
    can make a stale plan count as active (reproducing bugs in the
    field) or a fresh plan count as stale (tighter windows)."""
    plan = tmp_path / 'x.md'; plan.write_text('y')
    os.utime(plan, (1000, 1000))  # 1970
    fake = _FakeProvider(tmp_path)
    # Under default 3600s window: stale.
    assert prompt_trace._plan_is_active(provider=fake) is False
    # Under ridiculously wide window: still 'active'.
    import sys
    assert prompt_trace._plan_is_active(provider=fake, fresh_seconds=sys.maxsize) is True


def test_prompt_trace_swallows_emit_span_errors(monkeypatch):
    """Post-span failure must not propagate: the hook still has to
    return its HookResponse so the UserPromptSubmit pipeline completes."""
    def _boom(**_kw):
        raise RuntimeError('ingest down')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r = prompt_trace.handle(_p('UserPromptSubmit',
        prompt='hello world', session_id='s1'))
    assert r is not None
    assert r.suppress_output is True


# ── prompt_trace deterministic span_id ──────────────────────────────

def _write_jsonl(path, entries):
    import json
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n')


def test_prompt_trace_uses_deterministic_span_id_from_transcript(
    captured_spans, tmp_path,
):
    """The prompt span's span_id must be derivable from the transcript's
    most recent user-prompt entry uuid as `prompt-<uuid[:13]>`. This is
    the contract turn_trace relies on to compute parent_id for the
    assistant_response spans it emits later."""
    transcript = tmp_path / 'session.jsonl'
    user_uuid = 'user-abcdef0123456789'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': user_uuid, 'parentUuid': None,
         'message': {'content': 'do the thing'}},
    ])
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='do the thing',
                           session_id='s1',
                           transcript_path=str(transcript)))
    assert len(captured_spans) == 1
    assert captured_spans[0]['span_id'] == f'prompt-{user_uuid[:13]}'


def test_prompt_trace_skips_tool_result_user_entries(
    captured_spans, tmp_path,
):
    """Tool-result entries are also `type: user` in the transcript;
    they should NOT be picked as the prompt anchor. The latest *real*
    user prompt is the one we're submitting now."""
    transcript = tmp_path / 'session.jsonl'
    real_uuid = 'user-realprompt12345'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': real_uuid,
         'message': {'content': 'do the thing'}},
        {'type': 'assistant', 'uuid': 'asst-1', 'parentUuid': real_uuid,
         'message': {'content': []}},
        {'type': 'user', 'uuid': 'tool-result-uuid-xyz', 'parentUuid': 'asst-1',
         'message': {'content': [{'type': 'tool_result', 'content': 'output'}]}},
    ])
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='do the thing',
                           session_id='s1',
                           transcript_path=str(transcript)))
    assert captured_spans[0]['span_id'] == f'prompt-{real_uuid[:13]}'


def test_prompt_trace_falls_back_when_transcript_missing(captured_spans):
    """If transcript_path is absent or unreadable, the span still
    gets emitted — just without a deterministic span_id (post_span
    generates a random one). The whole prompt_trace pipeline must
    not crash on missing transcripts."""
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt='hello',
                           session_id='s1'))
    assert len(captured_spans) == 1
    # span_id is None here; post_span itself fills it with a random hex
    # at build time — so from the captured kw we just verify the call
    # didn't pass an explicit deterministic id.
    assert captured_spans[0].get('span_id') is None


# ── prompt_trace task.notification handling ─────────────────────────


_TASK_NOTIFICATION_SAMPLE = (
    '<task-notification>\n'
    '<task-id>b5apsteg4</task-id>\n'
    '<tool-use-id>toolu_01TwNe6zgbExFztAJ64gvShD</tool-use-id>\n'
    '<output-file>/tmp/tasks/b5apsteg4.output</output-file>\n'
    '<status>completed</status>\n'
    '<summary>Background command "Retry embed" completed (exit code 0)</summary>\n'
    '</task-notification>'
)


def test_prompt_trace_emits_task_notification_span(captured_spans):
    prompt_trace.handle(_p('UserPromptSubmit',
                           prompt=_TASK_NOTIFICATION_SAMPLE, session_id='s1'))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'task.notification'
    assert s['trace_id'] == 's1'
    a = s['attributes']
    assert a['task_id'] == 'b5apsteg4'
    assert a['tool_use_id'] == 'toolu_01TwNe6zgbExFztAJ64gvShD'
    assert a['output_file'] == '/tmp/tasks/b5apsteg4.output'
    assert a['status'] == 'completed'
    assert a['summary'].startswith('Background command "Retry embed"')
    assert s['span_id'] == 'task-b5apsteg4'  # deterministic from task_id


def test_prompt_trace_task_notification_does_not_trigger_plan_decision(
    captured_spans, tmp_path
):
    """A task-notification with the word 'completed' in its status must
    not be misread as a plan approval keyword."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    (plans_dir / 'p.md').write_text('plan')
    payload = _payload_with_provider('UserPromptSubmit', str(plans_dir),
                                     prompt=_TASK_NOTIFICATION_SAMPLE,
                                     session_id='s1')
    r = prompt_trace.handle(payload)
    assert r is not None
    assert r.additional_context is None


# ── post_tool_trace ───────────────────────────────────────────────────

def test_post_tool_trace_skips_missing_tool_name():
    r = post_tool_trace.handle(_p('PostToolUse', tool_name=None))
    assert r is None


def test_post_tool_trace_bash_posts_span(captured_spans):
    long = 'echo ' + ('x' * 500)
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash', tool_input={'command': long}))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'tool.Bash'
    assert s['attributes']['tool_name'] == 'Bash'
    preview = s['attributes']['command_preview']
    assert preview.endswith('…')
    assert len(preview) <= post_tool_trace._PREVIEW_MAX + 1
    # Long commands also carry the full text so the expanded UI panel
    # can show what the 200-char preview hides.
    assert s['attributes']['command'] == long
    assert 'command_truncated_bytes' not in s['attributes']


def test_post_tool_trace_bash_short_command_omits_full(captured_spans):
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash', tool_input={'command': 'ls -la'}))
    attrs = captured_spans[0]['attributes']
    assert attrs['command_preview'] == 'ls -la'
    # Short commands don't need a second copy — the preview already
    # contains the whole thing.
    assert 'command' not in attrs


def test_post_tool_trace_bash_oversized_command_truncated(captured_spans):
    huge = 'x' * (post_tool_trace._BASH_COMMAND_MAX + 1234)
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash', tool_input={'command': huge}))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['command']) == post_tool_trace._BASH_COMMAND_MAX
    assert attrs['command_truncated_bytes'] == 1234


def test_post_tool_trace_mcp_tool_marked(captured_spans):
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='mcp__foo__bar',
        tool_input={'a': 1}))
    assert captured_spans[0]['name'] == 'tool.mcp__foo__bar'
    assert captured_spans[0]['attributes']['mcp'] is True


def test_post_tool_trace_edit_records_file(captured_spans):
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={'file_path': '/tmp/a.py'}))
    assert captured_spans[0]['attributes']['file_path'] == '/tmp/a.py'


def test_post_tool_trace_edit_computes_unified_diff(captured_spans):
    """Edit spans carry a server-computed unified diff plus
    added/removed line counts so the WebUI can render a diff card
    without re-running difflib in the browser."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={
            'file_path': '/tmp/a.py',
            'old_string': 'def foo():\n    return 1\n',
            'new_string': 'def foo():\n    return 2\n',
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['edit_op'] == 'edit'
    assert attrs['added_lines'] == 1
    assert attrs['removed_lines'] == 1
    assert '-    return 1' in attrs['diff']
    assert '+    return 2' in attrs['diff']
    # Header lines (--- / +++) are stripped — the UI synthesises its own
    assert '---' not in attrs['diff']
    assert '+++' not in attrs['diff']


def test_post_tool_trace_write_diff_is_all_additions(captured_spans):
    """Write has no `old_string` — render the whole content as additions
    so the diff card shows a `Create(path)` view."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Write',
        tool_input={
            'file_path': '/tmp/new.py',
            'content': 'line1\nline2\nline3\n',
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['edit_op'] == 'write'
    assert attrs['added_lines'] == 3
    assert attrs['removed_lines'] == 0
    assert '+line1' in attrs['diff']
    assert '+line2' in attrs['diff']


def test_post_tool_trace_multi_edit_concatenates_diffs(captured_spans):
    """MultiEdit's per-edit (old,new) pairs are diffed individually and
    concatenated; total line counts are summed across edits."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='MultiEdit',
        tool_input={
            'file_path': '/tmp/a.py',
            'edits': [
                {'old_string': 'a\n', 'new_string': 'A\n'},
                {'old_string': 'b\n', 'new_string': 'B\nC\n'},
            ],
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['edit_op'] == 'multi_edit'
    # 1 removed + 1 added from edit#1, 1 removed + 2 added from edit#2
    assert attrs['removed_lines'] == 2
    assert attrs['added_lines'] == 3
    assert '-a' in attrs['diff'] and '+A' in attrs['diff']
    assert '-b' in attrs['diff'] and '+B' in attrs['diff']


def test_post_tool_trace_edit_truncates_giant_diff(captured_spans):
    """An edit big enough to overflow the diff cap must truncate at a
    newline (never mid-line) and record the dropped bytes — partial
    final lines would render as half-rendered red/green rows."""
    # Enough distinct lines to comfortably exceed _EDIT_DIFF_MAX so the
    # test stays meaningful even if the cap is bumped later.
    n_lines = (post_tool_trace._EDIT_DIFF_MAX // 8) + 1
    old = '\n'.join(f'old line {i}' for i in range(n_lines))
    new = '\n'.join(f'new line {i}' for i in range(n_lines))
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={'file_path': '/tmp/big.py', 'old_string': old, 'new_string': new}))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['diff']) <= post_tool_trace._EDIT_DIFF_MAX
    assert attrs['diff_truncated_bytes'] > 0
    # No trailing partial line.
    assert not attrs['diff'].endswith(('+', '-', '@'))


def test_post_tool_trace_edit_with_no_change_omits_diff(captured_spans):
    """If old_string == new_string the diff is empty — don't attach
    `edit_op`/`diff` attrs that would make the UI think there's a diff
    to render."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={
            'file_path': '/tmp/a.py',
            'old_string': 'same\n', 'new_string': 'same\n',
        }))
    attrs = captured_spans[0]['attributes']
    assert 'diff' not in attrs
    assert 'edit_op' not in attrs


def test_post_tool_trace_glob_records_pattern(captured_spans):
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Glob',
        tool_input={'pattern': '**/*.py'}))
    assert captured_spans[0]['attributes']['pattern'] == '**/*.py'


def test_post_tool_trace_grep_records_pattern(captured_spans):
    """Grep is exercised parallel to Glob — without a dedicated test a
    refactor could quietly drop pattern capture for one tool while
    keeping it for the other, and the trace view would lose half its
    search-intent visibility."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Grep',
        tool_input={'pattern': 'log.*Error'}))
    assert captured_spans[0]['name'] == 'tool.Grep'
    assert captured_spans[0]['attributes']['pattern'] == 'log.*Error'


def test_post_tool_trace_uses_notebook_path_fallback(captured_spans):
    """_file_path falls back through file_path → path → notebook_path.
    NotebookEdit sends notebook_path, not file_path — without the
    fallback, every notebook edit would lose its file association."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='NotebookEdit',
        tool_input={'notebook_path': '/tmp/notebook.ipynb'}))
    assert captured_spans[0]['attributes']['file_path'] == '/tmp/notebook.ipynb'


def test_post_tool_trace_uses_path_fallback(captured_spans):
    """Some MCP tools ship `path` instead of `file_path`. The fallback
    should still record it on the span."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='mcp__fs__read',
        tool_input={'path': '/srv/data.txt'}))
    assert captured_spans[0]['attributes']['file_path'] == '/srv/data.txt'


def test_post_tool_trace_plain_tool_emits_only_name(captured_spans):
    """Read without a tool_response should still emit exactly ONE span
    and not require any of the Bash/Glob/etc. specific attrs. With no
    `tool_response.file`, content capture is a no-op."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Read',
        tool_input={'file_path': '/tmp/a'}))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'tool.Read'
    assert s['attributes']['tool_name'] == 'Read'
    assert 'command_preview' not in s['attributes']
    assert 'pattern' not in s['attributes']
    assert 'content' not in s['attributes']


def test_post_tool_trace_read_captures_content_and_line_metadata(captured_spans):
    """Read tool_response carries the file body the model actually saw
    plus the line slice it read. Replaying a session needs both — file
    might have been re-read from a different offset."""
    body = '\n'.join(f'line {i}' for i in range(50))
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Read',
        tool_input={'file_path': '/tmp/a.py'},
        tool_response={
            'type': 'text',
            'file': {
                'filePath': '/tmp/a.py',
                'content': body,
                'numLines': 50,
                'startLine': 1,
                'totalLines': 200,
            },
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['content'] == body
    assert attrs['num_lines'] == 50
    assert attrs['start_line'] == 1
    assert attrs['total_lines'] == 200
    assert 'content_truncated_bytes' not in attrs


def test_post_tool_trace_read_truncates_oversized_content(captured_spans):
    """When the file body exceeds the cap, store the head and record
    the dropped byte count so the UI can show "+N bytes elided"."""
    huge = 'x' * (post_tool_trace._READ_CONTENT_MAX + 1234)
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Read',
        tool_input={'file_path': '/tmp/big.txt'},
        tool_response={'file': {'content': huge}}))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['content']) == post_tool_trace._READ_CONTENT_MAX
    assert attrs['content_truncated_bytes'] == 1234


def test_post_tool_trace_edit_captures_user_modified_and_hunks(captured_spans):
    """`user_modified=True` is the signal that the user hand-edited the
    diff in the Claude Code UI before applying — it explains divergence
    between what the model wrote and what landed. structured_patch
    hunks give the trace UI line ranges without re-running difflib."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={
            'file_path': '/tmp/a.py',
            'old_string': 'def foo():\n    return 1\n',
            'new_string': 'def foo():\n    return 2\n',
        },
        tool_response={
            'user_modified': True,
            'replace_all': False,
            'structured_patch': [
                {'oldStart': 10, 'oldLines': 2, 'newStart': 10, 'newLines': 2},
            ],
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['user_modified'] is True
    assert 'replace_all' not in attrs  # only emitted when True
    assert attrs['hunks'] == [
        {'old_start': 10, 'old_lines': 2, 'new_start': 10, 'new_lines': 2},
    ]


def test_post_tool_trace_edit_skips_metadata_when_absent(captured_spans):
    """No user_modified flag, no structured_patch → no metadata attrs.
    Don't surface defaults that lie about what the response said."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Edit',
        tool_input={
            'file_path': '/tmp/a.py',
            'old_string': 'a\n', 'new_string': 'b\n',
        }))
    attrs = captured_spans[0]['attributes']
    assert 'user_modified' not in attrs
    assert 'replace_all' not in attrs
    assert 'hunks' not in attrs


def test_post_tool_trace_ask_user_question_records_qa(captured_spans):
    """AskUserQuestion's question + the user's answer must end up on the
    span so the session-trace view can show them; otherwise the only
    user-facing turn during planning is invisible in the timeline."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='AskUserQuestion',
        tool_input={
            'questions': [
                {
                    'question': 'Pick a path',
                    'header': 'Source',
                    'options': [
                        {'label': 'A', 'description': 'first'},
                        {'label': 'B', 'description': 'second'},
                    ],
                    'multiSelect': False,
                },
            ],
        },
        tool_response={
            'answers': {'Pick a path': 'A'},
            'annotations': {'Pick a path': {'notes': 'because reasons'}},
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['questions'][0]['question'] == 'Pick a path'
    assert attrs['questions'][0]['options'] == [
        {'label': 'A', 'description': 'first'},
        {'label': 'B', 'description': 'second'},
    ]
    assert attrs['answers'] == {'Pick a path': 'A'}
    assert attrs['annotations'] == {'Pick a path': {'notes': 'because reasons'}}


def test_post_tool_trace_tool_search_select_breaks_out_names(captured_spans):
    """`select:a,b,c` is the common deferred-tool-loading shape; the
    parsed list lets the UI render a clean `ToolSearch: a, b, c` row
    without re-parsing the colon prefix on the frontend."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='ToolSearch',
        tool_input={
            'query': 'select:mcp__plug__browser_navigate,mcp__plug__browser_click',
            'max_results': 5,
        }))
    attrs = captured_spans[0]['attributes']
    assert captured_spans[0]['name'] == 'tool.ToolSearch'
    assert attrs['query'] == 'select:mcp__plug__browser_navigate,mcp__plug__browser_click'
    assert attrs['selected_tools'] == [
        'mcp__plug__browser_navigate', 'mcp__plug__browser_click',
    ]
    assert attrs['max_results'] == 5


def test_post_tool_trace_tool_search_freeform_query_records_only_text(captured_spans):
    """Non-`select:` queries are keyword searches — keep the raw text
    and skip the parsed list so the UI knows to render the query
    string verbatim instead of pretending it found exact tool names."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='ToolSearch',
        tool_input={'query': '+slack send'}))
    attrs = captured_spans[0]['attributes']
    assert attrs['query'] == '+slack send'
    assert 'selected_tools' not in attrs
    assert 'max_results' not in attrs


def test_post_tool_trace_tool_search_captures_response_matches(captured_spans):
    """The hook's `tool_response.matches` is the authoritative list of
    tools that actually got loaded — for keyword queries it's the only
    place the matched names appear at all. Preserve it as
    `loaded_tools` so the UI can show what the search returned, not
    just what was asked for."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='ToolSearch',
        tool_input={'query': '+slack send'},
        tool_response={
            'matches': ['mcp__slack__send_message', 'mcp__slack__send_dm'],
            'query': '+slack send',
            'total_deferred_tools': 42,
        }))
    attrs = captured_spans[0]['attributes']
    assert attrs['loaded_tools'] == [
        'mcp__slack__send_message', 'mcp__slack__send_dm',
    ]
    assert attrs['total_deferred_tools'] == 42


def test_post_tool_trace_short_bash_has_no_ellipsis(captured_spans):
    """Below the preview cap, the command is copied verbatim — adding
    an ellipsis to every short command would silently corrupt the preview."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash',
        tool_input={'command': 'ls -la'}))
    assert captured_spans[0]['attributes']['command_preview'] == 'ls -la'


def test_post_tool_trace_bash_captures_stdout_stderr(captured_spans):
    """The WebUI renders bash output under the command — so stdout and
    stderr must land on the span. Without this, the only thing the trace
    shows is the command itself."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash',
        tool_input={'command': 'ls'},
        tool_response={'stdout': 'a\nb\nc', 'stderr': '', 'interrupted': False}))
    attrs = captured_spans[0]['attributes']
    assert attrs['stdout'] == 'a\nb\nc'
    assert 'stderr' not in attrs  # empty stderr omitted
    assert 'interrupted' not in attrs
    assert 'stdout_truncated_bytes' not in attrs


def test_post_tool_trace_bash_truncates_long_output(captured_spans):
    """A runaway `cat huge.log` must not bloat the SessionSpan row.
    Outputs are capped at 8 KB stdout / 4 KB stderr; the dropped-bytes
    marker lets the UI show how much was lost."""
    long_out = 'x' * (post_tool_trace._BASH_STDOUT_MAX + 500)
    long_err = 'e' * (post_tool_trace._BASH_STDERR_MAX + 50)
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash',
        tool_input={'command': 'cat'},
        tool_response={'stdout': long_out, 'stderr': long_err}))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['stdout']) == post_tool_trace._BASH_STDOUT_MAX
    assert attrs['stdout_truncated_bytes'] == 500
    assert len(attrs['stderr']) == post_tool_trace._BASH_STDERR_MAX
    assert attrs['stderr_truncated_bytes'] == 50


def test_post_tool_trace_bash_records_interrupt(captured_spans):
    """The `interrupted` flag distinguishes a Ctrl-C'd command from one
    that exited cleanly — surface it on the span so the UI can mark it."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash',
        tool_input={'command': 'sleep 99'},
        tool_response={'stdout': '', 'stderr': '', 'interrupted': True}))
    attrs = captured_spans[0]['attributes']
    assert attrs['interrupted'] is True


def test_post_tool_trace_always_returns_suppress_output(captured_spans):
    """Every response from this handler must keep suppress_output=True.
    Flipping that would re-expose tool input/response on the transcript
    — the exact noise the fa3922e silent-trace policy removed."""
    r = post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash', tool_input={'command': 'pwd'}))
    assert r and r.suppress_output is True
    assert r.additional_context is None


def test_post_tool_trace_swallows_emit_span_errors(monkeypatch):
    """If post_span raises (server down, malformed span, etc.), the
    handler must still return a clean response — a hook that crashes
    would leave the user with no tool feedback at all."""
    def _boom(**kw):
        raise RuntimeError('ingest down')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r = post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='Bash', tool_input={'command': 'pwd'}))
    assert r and r.suppress_output is True


def test_post_tool_trace_task_output_captures_response(captured_spans):
    """TaskOutput's whole signal lives in `tool_response.task` — without
    a dedicated branch the span only carries `tool_name`/`tool_use_id`
    and the conversation view shows a bare "TaskOutput" row with no
    task id, status, exit code, or captured output."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='TaskOutput',
        tool_input={'task_id': 'b8i2d8rdd', 'block': True, 'timeout': 300000},
        tool_response={
            'retrieval_status': 'success',
            'task': {
                'task_id': 'b8i2d8rdd',
                'task_type': 'local_bash',
                'status': 'completed',
                'exit_code': 0,
                'description': 'Run full suite',
                'output': 'E   sqlalchemy.exc.IntegrityError: …',
            },
        }))
    attrs = captured_spans[0]['attributes']
    assert captured_spans[0]['name'] == 'tool.TaskOutput'
    assert attrs['task_id'] == 'b8i2d8rdd'
    assert attrs['retrieval_status'] == 'success'
    assert attrs['task_type'] == 'local_bash'
    assert attrs['status'] == 'completed'
    assert attrs['exit_code'] == 0
    assert attrs['description'] == 'Run full suite'
    assert attrs['output'].startswith('E   sqlalchemy.exc.IntegrityError')
    assert 'output_truncated_bytes' not in attrs


def test_post_tool_trace_task_output_truncates_long_output(captured_spans):
    """A long-running task can dump megabytes of logs; the captured
    output is capped at _BASH_STDOUT_MAX with a `_truncated_bytes`
    marker so the trace row stays bounded."""
    long_out = 'y' * (post_tool_trace._BASH_STDOUT_MAX + 17)
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='TaskOutput',
        tool_input={'task_id': 'xyz'},
        tool_response={
            'retrieval_status': 'success',
            'task': {'task_id': 'xyz', 'status': 'completed', 'output': long_out},
        }))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['output']) == post_tool_trace._BASH_STDOUT_MAX
    assert attrs['output_truncated_bytes'] == 17


def test_post_tool_trace_task_output_failed_task_keeps_span_ok(captured_spans):
    """The wrapped task can exit non-zero, but the TaskOutput *tool
    call* itself succeeded — the span status_code must stay OK; the
    failure signal lives on `exit_code`. Flipping the span to ERROR
    would mis-color every poll the model makes on a known-failed
    background task."""
    post_tool_trace.handle(_p('PostToolUse', session_id='s1',
        tool_name='TaskOutput',
        tool_input={'task_id': 'failtask'},
        tool_response={
            'retrieval_status': 'success',
            'task': {'task_id': 'failtask', 'status': 'completed', 'exit_code': 1,
                     'output': 'boom'},
        }))
    span = captured_spans[0]
    assert span['attributes']['exit_code'] == 1
    assert 'status_code' not in span or span.get('status_code') in (None, 'OK')
