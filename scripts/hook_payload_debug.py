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

_LOG_PATH = os.path.expanduser('~/.claude/hook-payloads.jsonl')


def main() -> None:
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

    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    with open(_LOG_PATH, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    # Emit a schema-valid response so Claude Code continues normally.
    # Only PreToolUse/UserPromptSubmit/PostToolUse accept hookSpecificOutput.
    events_with_specific_output = {'PreToolUse', 'UserPromptSubmit', 'PostToolUse'}
    resp: dict = {'suppressOutput': True}
    if entry['hook_event'] in events_with_specific_output:
        resp['hookSpecificOutput'] = {
            'hookEventName': entry['hook_event'],
            'additionalContext': 'hook-payload-debug: logged',
        }
    json.dump(resp, sys.stdout)
    sys.stdout.write('\n')
    sys.stdout.flush()


if __name__ == '__main__':
    main()
