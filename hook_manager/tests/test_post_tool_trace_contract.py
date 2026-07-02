"""Contract-pin tests for hook_manager.handlers.post_tool_trace._emit_span.

Locks the exact `attributes` dict that gets passed to `lib.hook_plugin.post_span`
for every supported tool, so a subsequent refactor of the 260-line `_emit_span`
dispatch chain can't quietly drift the span attrs each tool produces.

Run before refactoring (should pass), then again after (should still pass).
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace as ptt


@pytest.fixture
def captured(monkeypatch):
    """Replace post_span with a recorder; return list of (trace_id, name, attrs)."""
    calls: list[tuple] = []

    def _fake_post_span(trace_id, name, attributes, **kwargs):
        calls.append((trace_id, name, attributes))

    # post_tool_trace imports post_span lazily inside _emit_span, so we patch
    # the source module — `lib.hook_plugin.post_span` — rather than a re-exported
    # attribute on the handler.
    import lib.hook_plugin as plugin
    monkeypatch.setattr(plugin, 'post_span', _fake_post_span)
    return calls


def _make_payload(tool_name, tool_input=None, tool_response=None, raw_extras=None):
    raw = {
        'hook_event_name': 'PostToolUse',
        'session_id': 'sess-1',
        'tool_name': tool_name,
        'tool_input': tool_input or {},
        'tool_response': tool_response or {},
    }
    if raw_extras:
        raw.update(raw_extras)
    return HookPayload.from_stdin_json('PostToolUse', raw)


# ── Common attrs ────────────────────────────────────────────────

def test_common_tool_name_always_set(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'}))
    assert captured[0][2]['tool_name'] == 'Glob'


def test_common_tool_use_id_pulled_from_raw(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'},
                             raw_extras={'tool_use_id': 'toolu_abc'}))
    assert captured[0][2]['tool_use_id'] == 'toolu_abc'


def test_common_file_path_from_file_path_key(captured):
    ptt.handle(_make_payload('Edit', {'file_path': '/x/y.py',
                                       'old_string': 'a', 'new_string': 'b'}))
    assert captured[0][2]['file_path'] == '/x/y.py'


def test_common_agent_id_and_agent_type_attached(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'},
                             raw_extras={'agent_id': 'agt-1', 'agent_type': 'Explore'}))
    attrs = captured[0][2]
    assert attrs['agent_id'] == 'agt-1'
    assert attrs['agent_type'] == 'Explore'


def test_common_span_name_uses_tool_prefix(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'}))
    assert captured[0][1] == 'tool.Glob'


def test_common_trace_id_is_session_id(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'}))
    assert captured[0][0] == 'sess-1'


# ── Bash ────────────────────────────────────────────────────────

def test_bash_preview_and_command_short(captured):
    ptt.handle(_make_payload('Bash',
                             {'command': 'ls -la'},
                             {'stdout': 'out\n', 'stderr': '', 'interrupted': False}))
    attrs = captured[0][2]
    assert attrs['command_preview'] == 'ls -la'
    assert 'command' not in attrs  # short command not duplicated
    assert attrs['stdout'] == 'out\n'
    assert 'stderr' not in attrs  # empty stderr not attached


def test_bash_long_command_stored_with_truncation(captured):
    long_cmd = 'x' * (ptt._PREVIEW_MAX + 100)
    ptt.handle(_make_payload('Bash', {'command': long_cmd},
                             {'stdout': '', 'stderr': ''}))
    attrs = captured[0][2]
    assert 'command' in attrs
    # _BASH_COMMAND_MAX is huge, so no truncation expected at this size
    assert 'command_truncated_bytes' not in attrs


def test_bash_interrupted_flag(captured):
    ptt.handle(_make_payload('Bash', {'command': 'sleep 9'},
                             {'stdout': '', 'stderr': '', 'interrupted': True}))
    assert captured[0][2]['interrupted'] is True


# ── Edit / Write / MultiEdit ─────────────────────────────────────

def test_edit_attaches_diff_and_metadata(captured):
    ptt.handle(_make_payload('Edit',
                             {'file_path': '/x.py',
                              'old_string': 'a\n', 'new_string': 'b\n'},
                             {'user_modified': True, 'replace_all': False}))
    attrs = captured[0][2]
    assert attrs['edit_op'] == 'edit'
    assert attrs['added_lines'] >= 1
    assert attrs['removed_lines'] >= 1
    assert 'diff' in attrs
    assert attrs['user_modified'] is True


def test_write_attaches_diff_with_write_op(captured):
    ptt.handle(_make_payload('Write',
                             {'file_path': '/x.py', 'content': 'hello\n'},
                             {}))
    attrs = captured[0][2]
    assert attrs['edit_op'] == 'write'
    assert 'diff' in attrs


def test_multiedit_combines_diffs(captured):
    ptt.handle(_make_payload('MultiEdit',
                             {'file_path': '/x.py',
                              'edits': [
                                  {'old_string': 'a\n', 'new_string': 'b\n'},
                                  {'old_string': 'c\n', 'new_string': 'd\n'},
                              ]},
                             {}))
    attrs = captured[0][2]
    assert attrs['edit_op'] == 'multi_edit'
    assert 'diff' in attrs
    assert attrs['added_lines'] >= 2
    assert attrs['removed_lines'] >= 2


# ── Read ────────────────────────────────────────────────────────

def test_read_attaches_content_and_lines_snake_case(captured):
    ptt.handle(_make_payload('Read',
                             {'file_path': '/x.py'},
                             {'file': {'content': 'line1\nline2\n',
                                       'num_lines': 2,
                                       'start_line': 1,
                                       'total_lines': 100}}))
    attrs = captured[0][2]
    assert attrs['content'] == 'line1\nline2\n'
    assert attrs['num_lines'] == 2
    assert attrs['start_line'] == 1
    assert attrs['total_lines'] == 100


def test_read_accepts_camel_case_alternate_keys(captured):
    ptt.handle(_make_payload('Read',
                             {'file_path': '/x.py'},
                             {'file': {'content': 'x',
                                       'numLines': 5,
                                       'startLine': 2,
                                       'totalLines': 50}}))
    attrs = captured[0][2]
    assert attrs['num_lines'] == 5
    assert attrs['start_line'] == 2
    assert attrs['total_lines'] == 50


def test_read_break_semantics_first_valid_wins(captured):
    # If both spellings present, the first one in the tuple wins (snake_case).
    ptt.handle(_make_payload('Read',
                             {'file_path': '/x.py'},
                             {'file': {'content': 'x',
                                       'num_lines': 2, 'numLines': 99}}))
    assert captured[0][2]['num_lines'] == 2


# ── Glob / Grep ─────────────────────────────────────────────────

def test_glob_attaches_pattern(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '**/*.py'}))
    assert captured[0][2]['pattern'] == '**/*.py'


def test_grep_attaches_pattern(captured):
    ptt.handle(_make_payload('Grep', {'pattern': 'TODO'}))
    assert captured[0][2]['pattern'] == 'TODO'


# ── AskUserQuestion ─────────────────────────────────────────────

def test_ask_user_question_preserves_options_and_answers(captured):
    ptt.handle(_make_payload(
        'AskUserQuestion',
        {'questions': [{'question': 'Which?',
                        'header': 'X',
                        'options': [{'label': 'A', 'description': 'first'},
                                    {'label': 'B'}],
                        'multiSelect': False}]},
        {'answers': {'Which?': 'A'}, 'annotations': {'Which?': {'notes': 'n'}}}))
    attrs = captured[0][2]
    assert attrs['questions'][0]['question'] == 'Which?'
    assert attrs['questions'][0]['options'][0] == {'label': 'A', 'description': 'first'}
    assert attrs['questions'][0]['options'][1] == {'label': 'B'}
    assert attrs['questions'][0]['multiSelect'] is False
    assert attrs['answers'] == {'Which?': 'A'}
    assert attrs['annotations'] == {'Which?': {'notes': 'n'}}


# ── ToolSearch ──────────────────────────────────────────────────

def test_toolsearch_select_query_extracts_tool_names(captured):
    ptt.handle(_make_payload(
        'ToolSearch',
        {'query': 'select:Read,Edit,Grep', 'max_results': 5},
        {'matches': ['Read', 'Edit'], 'total_deferred_tools': 80}))
    attrs = captured[0][2]
    assert attrs['query'] == 'select:Read,Edit,Grep'
    assert attrs['selected_tools'] == ['Read', 'Edit', 'Grep']
    assert attrs['max_results'] == 5
    assert attrs['loaded_tools'] == ['Read', 'Edit']
    assert attrs['total_deferred_tools'] == 80


def test_toolsearch_keyword_query_no_selected_tools(captured):
    ptt.handle(_make_payload(
        'ToolSearch',
        {'query': 'jupyter notebook'},
        {'matches': ['NotebookEdit']}))
    attrs = captured[0][2]
    assert attrs['query'] == 'jupyter notebook'
    assert 'selected_tools' not in attrs
    assert attrs['loaded_tools'] == ['NotebookEdit']


# ── TaskCreate / TaskUpdate / TaskOutput ─────────────────────────

def test_taskcreate_captures_subject_and_task_id(captured):
    ptt.handle(_make_payload(
        'TaskCreate',
        {'subject': 'do thing', 'description': 'long desc',
         'activeForm': 'Doing thing'},
        {'task': {'id': 'tsk-1'}}))
    attrs = captured[0][2]
    assert attrs['subject'] == 'do thing'
    assert attrs['description'] == 'long desc'
    assert attrs['active_form'] == 'Doing thing'
    assert attrs['task_id'] == 'tsk-1'


def test_taskupdate_normalizes_taskid_camel_to_snake(captured):
    ptt.handle(_make_payload(
        'TaskUpdate', {'taskId': 'tsk-1', 'status': 'completed'}))
    attrs = captured[0][2]
    assert attrs['task_id'] == 'tsk-1'
    assert attrs['status'] == 'completed'


def test_taskoutput_captures_retrieval_and_wrapped_task_fields(captured):
    ptt.handle(_make_payload(
        'TaskOutput',
        {'task_id': 'tsk-1'},
        {'retrieval_status': 'success',
         'task': {'task_type': 'general-purpose', 'status': 'completed',
                  'description': 'desc', 'exit_code': 0,
                  'output': 'output text'}}))
    attrs = captured[0][2]
    assert attrs['task_id'] == 'tsk-1'
    assert attrs['retrieval_status'] == 'success'
    assert attrs['task_type'] == 'general-purpose'
    assert attrs['status'] == 'completed'
    assert attrs['description'] == 'desc'
    assert attrs['exit_code'] == 0
    assert attrs['output'] == 'output text'


# ── Skill ────────────────────────────────────────────────────────

def test_skill_captures_name_and_args(captured):
    ptt.handle(_make_payload(
        'Skill', {'skill': 'my-skill', 'args': 'arg1 arg2'}))
    attrs = captured[0][2]
    assert attrs['skill_name'] == 'my-skill'
    assert attrs['skill_args'] == 'arg1 arg2'


# ── Agent ────────────────────────────────────────────────────────

def test_agent_captures_description_prompt_and_subagent_type(captured):
    ptt.handle(_make_payload(
        'Agent',
        {'description': 'Find stale code', 'prompt': 'Search the repo for X',
         'subagent_type': 'Explore'}))
    attrs = captured[0][2]
    assert attrs['subagent_type'] == 'Explore'
    assert attrs['description'] == 'Find stale code'
    assert attrs['prompt'] == 'Search the repo for X'
    assert captured[0][1] == 'tool.Agent'


def test_agent_prompt_truncated_records_dropped_bytes(captured):
    long_prompt = 'p' * (ptt._BASH_STDOUT_MAX + 50)
    ptt.handle(_make_payload(
        'Agent', {'description': 'd', 'prompt': long_prompt, 'subagent_type': 'general-purpose'}))
    attrs = captured[0][2]
    assert len(attrs['prompt']) == ptt._BASH_STDOUT_MAX
    assert attrs['prompt_truncated_bytes'] == 50


# ── mcp__ prefix ─────────────────────────────────────────────────

def test_mcp_tool_gets_mcp_flag(captured):
    ptt.handle(_make_payload(
        'mcp__server__do_thing', {'arg': 1}))
    attrs = captured[0][2]
    assert attrs['mcp'] is True
    assert attrs['tool_name'] == 'mcp__server__do_thing'


def test_mcp_span_name_uses_full_tool_name(captured):
    ptt.handle(_make_payload(
        'mcp__server__do_thing', {'arg': 1}))
    assert captured[0][1] == 'tool.mcp__server__do_thing'


# ── WebSearch / WebFetch ─────────────────────────────────────────

def test_websearch_captures_query(captured):
    ptt.handle(_make_payload('WebSearch', {'query': 'opus pricing'}))
    attrs = captured[0][2]
    assert attrs['query'] == 'opus pricing'
    assert captured[0][1] == 'tool.WebSearch'


def test_webfetch_captures_url_and_prompt(captured):
    ptt.handle(_make_payload(
        'WebFetch',
        {'url': 'https://example.com/doc', 'prompt': 'extract the pricing table'}))
    attrs = captured[0][2]
    assert attrs['url'] == 'https://example.com/doc'
    assert attrs['fetch_prompt'] == 'extract the pricing table'


def test_webfetch_long_prompt_truncated(captured):
    long_prompt = 'p' * (ptt._PREVIEW_MAX + 40)
    ptt.handle(_make_payload(
        'WebFetch', {'url': 'https://x', 'prompt': long_prompt}))
    attrs = captured[0][2]
    assert len(attrs['fetch_prompt']) == ptt._PREVIEW_MAX
    assert attrs['fetch_prompt_truncated_bytes'] == 40


# ── prompt_id → source_prompt_id (Claude Code 2.1.195+) ──────────

def test_source_prompt_id_captured_from_raw(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'},
                             raw_extras={'prompt_id': 'pr-uuid-123'}))
    assert captured[0][2]['source_prompt_id'] == 'pr-uuid-123'


def test_source_prompt_id_absent_when_no_prompt_id(captured):
    ptt.handle(_make_payload('Glob', {'pattern': '*.py'}))
    assert 'source_prompt_id' not in captured[0][2]


# ── Bash git commit (Claude Code 2.1.195+ tool_response.git_operation) ──

def test_bash_git_commit_captured(captured):
    ptt.handle(_make_payload(
        'Bash', {'command': 'git commit -m x'},
        tool_response={'git_operation': {'commit': {'sha': '8e48620', 'kind': 'create'}}}))
    attrs = captured[0][2]
    assert attrs['git_commit_sha'] == '8e48620'
    assert attrs['git_op_kind'] == 'create'


def test_bash_git_commit_absent_for_non_git_call(captured):
    ptt.handle(_make_payload('Bash', {'command': 'ls'},
                             tool_response={'stdout': 'file.txt'}))
    attrs = captured[0][2]
    assert 'git_commit_sha' not in attrs
    assert 'git_op_kind' not in attrs


# ── No tool_name → no-op ─────────────────────────────────────────

def test_no_tool_name_does_not_emit(captured):
    payload = HookPayload.from_stdin_json('PostToolUse', {
        'hook_event_name': 'PostToolUse',
        'session_id': 'sess-1',
    })
    result = ptt.handle(payload)
    assert result is None
    assert captured == []
