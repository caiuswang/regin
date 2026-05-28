#!/usr/bin/env python3
"""Print a migration-safety report before cutting ~/.claude/settings.json over.

Compares the user's current settings.json against what hook_manager would
produce. Emits a human-readable report to stdout; writes nothing.

Usage:
    python -m hook_manager.migration_preview [--settings PATH]

Exit codes:
    0 = report printed (even if no current hooks exist)
    1 = cannot read the settings file
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import SPEC_EVENTS
from .registry import REGISTRY


def _covered_events() -> set[str]:
    covered: set[str] = set()
    for h in REGISTRY:
        for ev in h.events:
            if ev == '*':
                covered.update(SPEC_EVENTS)
            else:
                covered.add(ev)
    return covered


def _load_settings(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _current_hooks_by_event(settings: dict) -> dict[str, list[dict]]:
    hooks = settings.get('hooks') or {}
    return {
        ev: blocks for ev, blocks in hooks.items()
        if isinstance(blocks, list) and ev in SPEC_EVENTS
    }


def _summarize_current_command(block: dict) -> str:
    hooks = block.get('hooks') or []
    parts: list[str] = []
    for h in hooks:
        t = h.get('type', 'command')
        if t == 'command':
            cmd = h.get('command', '')
            parts.append(cmd if len(cmd) < 80 else cmd[:77] + '…')
        else:
            parts.append(f'<{t}>')
    return ' | '.join(parts) or '(empty)'


def _handlers_for_event(event: str) -> list[str]:
    matching = [h for h in REGISTRY if '*' in h.events or event in h.events]
    # Same order the runner uses: by priority, then name, so the report
    # reflects real execution order.
    matching.sort(key=lambda h: (h.priority, h.name))
    return [h.name for h in matching]


def _render(settings_path: str, settings: dict) -> str:
    lines: list[str] = []
    lines.append(f'# Migration preview for {settings_path}')
    lines.append('')

    current = _current_hooks_by_event(settings)
    covered = _covered_events()

    lines.append('## Coverage overview')
    lines.append('')
    lines.append(f'- Spec events total: {len(SPEC_EVENTS)}')
    lines.append(f'- Events with at least one hook in your current settings: {len(current)}')
    lines.append(f'- Events that hook_manager would cover if swapped in: {len(covered)}')
    lines.append('')

    lines.append('## Event-by-event plan')
    lines.append('')
    lines.append('| Event | Current hooks | hook_manager handlers (after swap) |')
    lines.append('|-------|---------------|-------------------------------------|')
    for ev in sorted(SPEC_EVENTS):
        cur_blocks = current.get(ev, [])
        cur_desc = '\n'.join(_summarize_current_command(b) for b in cur_blocks) or '—'
        cur_desc = cur_desc.replace('|', '\\|')
        handlers = _handlers_for_event(ev)
        post = ', '.join(handlers) if handlers else '— (unwired by design)'
        lines.append(f'| `{ev}` | {cur_desc} | {post} |')
    lines.append('')

    # Behaviors that change
    lines.append('## Behavior changes you should expect')
    lines.append('')
    changes: list[str] = []

    # 1. Debug-log context noise gone
    changes.append(
        '- **Transcript cleaner.** The legacy `hook_payload_debug.py` fires on '
        'every event and emits `additionalContext: "hook-payload-debug: logged"` '
        'into the model\'s transcript. The new `trace_payload` handler logs to '
        '`~/.claude/hook-payloads.jsonl` but emits no additionalContext.'
    )
    # 2. mvn gate still present but centralized
    changes.append(
        '- **Same mvn block, cleaner response.** `block_mvn` replaces the inline '
        '`jq | grep | echo` one-liner. Same behavior: `mvn …` commands are '
        'denied with a message pointing at the maven MCP tools.'
    )
    # 3. Commit guard generalized
    changes.append(
        '- **Commit guards consolidated.** Three near-identical settings.json '
        'blocks become one `commit_guard` handler. Same per-repo behavior; '
        'adding a new guarded repo is a one-line change in Python.'
    )
    # 4. New events now covered
    covered_new = sorted(covered - set(current.keys()))
    if covered_new:
        changes.append(
            '- **New events now observed:** '
            + ', '.join(f'`{e}`' for e in covered_new)
            + '.'
        )
    # 5. Log rotation
    changes.append(
        '- **Log rotation.** `~/.claude/hook-payloads.jsonl` now rotates at '
        '50 MB (currently ~11 MB). Prior: unbounded.'
    )
    # 6. Slow-handler observability
    changes.append(
        '- **Slow-handler observability.** Any handler taking > 500 ms is '
        'logged to `~/.claude/traces/hook-errors.jsonl` so regressions surface.'
    )

    lines.extend(changes)
    lines.append('')

    lines.append('Apply by replacing the `hooks` block in your settings file with `hook_manager/settings.example.json` (back up first).')
    lines.append('')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='hook_manager.migration_preview',
        description='Report what changes if you swap your settings.json hooks block.',
    )
    parser.add_argument(
        '--settings', default=os.path.expanduser('~/.claude/settings.json'),
        help='Path to settings.json (default: ~/.claude/settings.json)',
    )
    args = parser.parse_args(argv)

    try:
        settings = _load_settings(args.settings)
    except OSError as exc:
        print(f'error: cannot read {args.settings}: {exc}', file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f'error: malformed JSON in {args.settings}: {exc}', file=sys.stderr)
        return 1

    sys.stdout.write(_render(args.settings, settings))
    return 0


if __name__ == '__main__':
    sys.exit(main())
