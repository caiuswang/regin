"""Pins the exact attrs each PostToolUse builder emits for edge-shaped inputs
(missing keys, wrong types, over-cap text), plus `_mcp_result_text`'s shape
preference order."""

from __future__ import annotations

import pytest

from hook_manager.handlers import post_tool_builders as ptt

_BIG = 'x' * (ptt._BASH_STDOUT_MAX + 100)

_BUILDER_CASES = [
    ('_build_ask_attrs',
     {'questions': [{'question': 'Q', 'header': 'H',
                     'options': [{'label': 'a', 'description': 'd'}]}],
      'answers': {'Q': 'a'}},
     {},
     {'questions': [{'question': 'Q', 'header': 'H',
                     'options': [{'label': 'a', 'description': 'd'}],
                     'multiSelect': False}],
      'answers': {'Q': 'a'}}),
    ('_build_ask_attrs', {}, {'answers': {'Q': 'b'}, 'annotations': {'x': 1}},
     {'questions': [], 'answers': {'Q': 'b'}, 'annotations': {'x': 1}}),
    ('_build_ask_attrs', {'annotations': {'y': 2}}, {},
     {'questions': [], 'annotations': {'y': 2}}),
    ('_build_toolsearch_attrs', {'query': 'select:a, b,,c', 'max_results': 3},
     {'matches': ['a', 1, 'b'], 'total_deferred_tools': 9},
     {'query': 'select:a, b,,c', 'selected_tools': ['a', 'b', 'c'],
      'max_results': 3, 'loaded_tools': ['a', 'b'], 'total_deferred_tools': 9}),
    ('_build_toolsearch_attrs', {'query': 'select:', 'max_results': 'x'},
     {'matches': []}, {'query': 'select:'}),
    ('_build_toolsearch_attrs', {'query': 'free text'}, {'total_deferred_tools': '9'},
     {'query': 'free text'}),
    ('_build_toolsearch_attrs', {'query': 5}, {}, {}),
    ('_build_taskcreate_attrs',
     {'subject': 'S', 'description': 'DDD', 'activeForm': 'Doing'},
     {'task': {'id': 7}},
     {'subject': 'S', 'description': 'DDD', 'active_form': 'Doing', 'task_id': '7'}),
    ('_build_taskcreate_attrs', {'subject': '', 'activeForm': ''}, {'task_id': 't1'},
     {'task_id': 't1'}),
    ('_build_taskcreate_attrs', {}, {'task': 'notdict', 'taskId': 5}, {'task_id': '5'}),
    ('_build_taskcreate_attrs', {}, {'task': {}, 'task_id': 0}, {}),
    ('_build_taskcreate_attrs', {}, {}, {}),
    ('_build_taskoutput_attrs', {'taskId': 3},
     {'retrieval_status': 'success',
      'task': {'task_id': 9, 'task_type': 'bash', 'status': 'done',
               'description': '', 'exit_code': 1, 'output': 'out'}},
     {'task_id': '3', 'retrieval_status': 'success', 'task_type': 'bash',
      'status': 'done', 'exit_code': 1, 'output': 'out'}),
    ('_build_taskoutput_attrs', {}, {'task': {'task_id': 9, 'exit_code': True}},
     {'task_id': '9', 'exit_code': True}),
    ('_build_taskoutput_attrs', {}, {'task': {'exit_code': '1', 'output': ''}}, {}),
    ('_build_taskoutput_attrs', {}, {'retrieval_status': '', 'task': None}, {}),
]


@pytest.mark.parametrize('name,tool_input,tool_response,expected', _BUILDER_CASES)
def test_builder_attrs(name, tool_input, tool_response, expected):
    attrs: dict = {}
    getattr(ptt, name)(attrs, tool_input, tool_response, None)
    assert attrs == expected


@pytest.mark.parametrize('name,tool_input,tool_response,key', [
    ('_build_taskcreate_attrs', {'description': _BIG}, {}, 'description'),
    ('_build_taskoutput_attrs', {}, {'task': {'output': _BIG}}, 'output'),
])
def test_builder_truncates_over_cap_text(name, tool_input, tool_response, key):
    attrs: dict = {}
    getattr(ptt, name)(attrs, tool_input, tool_response, None)
    head, dropped = ptt._truncate_output(_BIG, ptt._BASH_STDOUT_MAX)
    assert (attrs[key], attrs[f'{key}_truncated_bytes']) == (head, dropped)


@pytest.mark.parametrize('response,expected', [
    ('s', 's'),
    (['x', {'text': 'a'}, {'text': 'b'}], 'a\nb'),
    ([{'type': 'image'}], '[\n  {\n    "type": "image"\n  }\n]'),
    ({'content': [{'text': 'c'}]}, 'c'),
    ({'content': 'str', 'result': 'r'}, 'r'),
    ({'result': 'r'}, 'r'),
    ({'result': 5}, '{\n  "result": 5\n}'),
    ({'content': []}, '{\n  "content": []\n}'),
    ([{'text': ''}], ''),
    ({'content': [{'text': 1}], 'result': 'r'}, '{\n  "content": [\n    {\n      "text": 1\n    }\n  ],\n  "result": "r"\n}'),
    (42, '42'),
    (None, 'null'),
])
def test_mcp_result_text_shapes(response, expected):
    assert ptt._mcp_result_text(response) == expected
