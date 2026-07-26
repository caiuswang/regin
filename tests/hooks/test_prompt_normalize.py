"""Prompt extraction across providers: Claude sends a string, Kimi a block list."""

from hook_manager.core import HookPayload, _extract_prompt


def test_claude_string_prompt_unchanged():
    assert _extract_prompt({'prompt': 'hello world'}) == 'hello world'
    assert _extract_prompt({'prompt': '   hello  \n'}) == 'hello'
    assert _extract_prompt({'text': 'from text'}) == 'from text'
    assert _extract_prompt({}) == ''


def test_kimi_content_block_list_prompt():
    assert _extract_prompt({'prompt': [{'type': 'text', 'text': 'hi'}]}) == 'hi'


def test_bare_string_parts_in_list():
    assert _extract_prompt({'prompt': ['a', 'b']}) == 'a\nb'


def test_multiple_text_blocks_are_joined():
    blocks = [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]
    assert _extract_prompt({'prompt': blocks}) == 'first\nsecond'


def test_non_text_blocks_are_skipped():
    blocks = [
        {'type': 'image', 'source': {'data': 'BASE64'}},
        {'type': 'text', 'text': ' describe this '},
    ]
    assert _extract_prompt({'prompt': blocks}) == 'describe this'


def test_empty_and_garbage_lists_fall_through():
    assert _extract_prompt({'prompt': []}) == ''
    assert _extract_prompt({'prompt': [{'type': 'image'}, 123, None]}) == ''
    assert _extract_prompt({'prompt': [], 'text': 'fallback'}) == 'fallback'
    assert _extract_prompt({'prompt': [{'type': 'image'}], 'text': 'fallback'}) == 'fallback'


def test_payload_from_stdin_json_normalizes_kimi_prompt():
    payload = HookPayload.from_stdin_json('UserPromptSubmit', {
        'hook_event_name': 'UserPromptSubmit',
        'session_id': 'session_113dcdf9-f20c-488a-a53a-619671b96ad2',
        'prompt': [{'type': 'text', 'text': 'give me the arch of current project'}],
    })
    assert payload.prompt == 'give me the arch of current project'


def test_payload_from_stdin_json_keeps_claude_prompt():
    payload = HookPayload.from_stdin_json('UserPromptSubmit', {
        'hook_event_name': 'UserPromptSubmit',
        'session_id': 'abc',
        'prompt': 'give me the arch of current project',
    })
    assert payload.prompt == 'give me the arch of current project'
