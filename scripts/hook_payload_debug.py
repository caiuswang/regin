#!/usr/bin/env python3
"""Generic hook payload debugger.

Logs every Claude Code hook payload to ~/.claude/hook-payloads.jsonl
so developers can inspect the exact shape of each event.

Install this for any hook event you want to trace:
  UserPromptSubmit, PostToolUse, PreToolUse, SessionStart, SessionEnd,
  Notification, etc. — see hook_manager.core.SPEC_EVENTS for the full list.
"""

import json
import os
import sys
from datetime import datetime

_DEFAULT_LOG_PATH = '~/.claude/hook-payloads.jsonl'


def _parse_args(args: list[str]) -> tuple[str, bool]:
    """Tiny arg parser: an optional log path and a `--silent` flag.

    The installer passes each provider's own log path (Kimi logs to
    ``~/.kimi-code/...`` not ``~/.claude/...``) and, for agents whose CLI shows
    raw hook stdout (Kimi), ``--silent`` so we emit nothing. With no args the
    Claude default is preserved byte-for-byte.
    """
    log = _DEFAULT_LOG_PATH
    silent = False
    for arg in args:
        if arg == '--silent':
            silent = True
        elif not arg.startswith('-'):
            log = arg
    return os.path.expanduser(log), silent


def main(argv: list[str] | None = None) -> None:
    log_path, silent = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {'_raw_error': 'could not parse stdin as JSON'}

    entry = {
        'received_at': datetime.now().isoformat(),
        'hook_event': payload.get('hook_event_name', 'Unknown'),
        'session_id': payload.get('session_id'),
        'payload': payload,
    }

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    # `--silent`: write nothing to stdout. Agents like Kimi Code render any
    # hook stdout verbatim in their UI, so even `{"suppressOutput": true}` —
    # which is Claude-only JSON — would show up as junk. The payload is already
    # captured to the log above; that is the whole job. Exit 0, silent.
    if silent:
        return

    # Claude default: emit a schema-valid response so it continues normally.
    # Deliberately NO `additionalContext`: this debugger fires on every event
    # (PreToolUse/PostToolUse/UserPromptSubmit/...), so echoing a line back
    # would inject "hook-payload-debug: logged" into the model's context on
    # every single tool call. We only suppress our own stdout so it never
    # reaches the transcript.
    resp: dict = {'suppressOutput': True}
    json.dump(resp, sys.stdout)
    sys.stdout.write('\n')
    sys.stdout.flush()


if __name__ == '__main__':
    main()
