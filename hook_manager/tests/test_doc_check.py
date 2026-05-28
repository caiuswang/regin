"""Tests for the doc-hygiene PostToolUse handler."""

from hook_manager.core import HookPayload
from hook_manager.handlers import doc_check


def _payload(tool_name: str, tool_input: dict) -> HookPayload:
    return HookPayload.from_stdin_json(
        'PostToolUse',
        {
            'hook_event_name': 'PostToolUse',
            'tool_name': tool_name,
            'tool_input': tool_input,
        },
    )


def test_skips_non_markdown_files():
    payload = _payload(
        'Write',
        {'file_path': '/tmp/foo.py', 'content': 'we have 13 blueprints here'},
    )
    assert doc_check.handle(payload) is None


def test_skips_non_edit_tools():
    payload = _payload(
        'Read',
        {'file_path': '/tmp/README.md'},
    )
    assert doc_check.handle(payload) is None


def test_flags_rot_prone_count_on_write():
    payload = _payload(
        'Write',
        {
            'file_path': '/repo/README.md',
            'content': 'The app has 13 blueprints and 27 views.',
        },
    )
    resp = doc_check.handle(payload)
    assert resp is not None
    assert resp.additional_context is not None
    assert '13 blueprints' in resp.additional_context
    assert '27 views' in resp.additional_context
    # Style warnings should never block.
    assert resp.permission_decision is None
    assert resp.decision is None


def test_flags_rot_prone_count_on_edit_new_string_only():
    # Old string mentions a rot-prone count, but it's being REMOVED — should
    # not be flagged. New string is clean — no warning.
    payload = _payload(
        'Edit',
        {
            'file_path': '/repo/ARCHITECTURE.md',
            'old_string': 'Flask backend (12 blueprints)',
            'new_string': 'Flask backend; see web/blueprints/',
        },
    )
    assert doc_check.handle(payload) is None


def test_flags_stale_phrase():
    payload = _payload(
        'Write',
        {
            'file_path': '/repo/docs/foo.md',
            'content': '# Foo\n\nLast updated: 2026-04-12\n\nContent.',
        },
    )
    resp = doc_check.handle(payload)
    assert resp is not None
    assert 'Last updated' in resp.additional_context


def test_multiedit_concatenates_new_strings():
    payload = _payload(
        'MultiEdit',
        {
            'file_path': '/repo/README.md',
            'edits': [
                {'old_string': 'a', 'new_string': 'we now have 47 modules'},
                {'old_string': 'b', 'new_string': 'and 113 tests'},
            ],
        },
    )
    resp = doc_check.handle(payload)
    assert resp is not None
    assert '47 modules' in resp.additional_context
    assert '113 tests' in resp.additional_context


def test_skips_when_no_rot_present():
    payload = _payload(
        'Write',
        {
            'file_path': '/repo/README.md',
            'content': (
                '# Title\n\n'
                'Three roles: admin, editor, viewer.\n'
                'See `web/blueprints/` for the blueprint list.\n'
            ),
        },
    )
    assert doc_check.handle(payload) is None


def test_skips_generated_paths():
    payload = _payload(
        'Write',
        {
            'file_path': '/repo/node_modules/foo/README.md',
            'content': 'irrelevant 99 modules',
        },
    )
    assert doc_check.handle(payload) is None
