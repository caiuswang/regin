"""Runner: parse stdin, dispatch to matching handlers, emit merged JSON."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Sequence

from lib.providers import get_active_provider

from .config import filter_enabled, priority_overrides
from .core import BLOCKABLE_VIA_EXIT_2, Handler, HookPayload, HookResponse, SPEC_EVENTS
from .merge import (
    kimi_block_reason,
    kimi_response_text,
    merge_responses,
    response_to_json,
)


_ERROR_LOG = os.path.join(str(get_active_provider().traces_dir()), 'hook-errors.jsonl')
# Log any handler whose wall-clock time exceeds this. Override via env var.
_SLOW_HANDLER_MS = int(os.environ.get('HOOK_MANAGER_SLOW_MS', '500'))


def _error_log_path(payload: HookPayload | None = None) -> str:
    if payload is not None:
        try:
            return os.path.join(str(payload.resolved_provider.traces_dir()), 'hook-errors.jsonl')
        except Exception:
            pass
    return _ERROR_LOG


def _log_slow(handler_name: str, event: str, elapsed_ms: float, payload: HookPayload | None = None) -> None:
    """Best-effort append of a slow-handler notice. Same format as _log_error
    so the same downstream ingest sees both."""
    try:
        path = _error_log_path(payload)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            'timestamp': datetime.now().isoformat(),
            'handler': handler_name,
            'event': event,
            'error_type': 'SlowHandler',
            'error': f'handler took {elapsed_ms:.0f} ms',
            'elapsed_ms': elapsed_ms,
        }
        with open(path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _log_error(handler_name: str, payload_event: str, exc: BaseException, payload: HookPayload | None = None) -> None:
    """Best-effort append to hook-errors.jsonl. Never raises."""
    try:
        path = _error_log_path(payload)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            'timestamp': datetime.now().isoformat(),
            'handler': handler_name,
            'event': payload_event,
            'error_type': type(exc).__name__,
            'error': str(exc),
            'traceback': traceback.format_exc(),
        }
        with open(path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


def run(
    event_hint: str,
    handlers: Sequence[Handler],
    stdin_text: str,
    stdout,
    agent_type: str | None = None,
) -> int:
    """Core runner (pure w.r.t. I/O — stdin and stdout are explicit).

    Returns the process exit code."""
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except (json.JSONDecodeError, ValueError):
        stdout.write('{}\n')
        return 0

    if agent_type and isinstance(data, dict):
        data.setdefault('agent_type', agent_type)

    payload = HookPayload.from_stdin_json(event_hint, data)

    if payload.event not in SPEC_EVENTS:
        _log_error('hook_manager', payload.event,
                   ValueError(f'unknown event {payload.event!r}'), payload)

    agent_id = getattr(payload.resolved_provider, 'provider_id', None)
    # Stash agent_type for span emission. build_span auto-injects it
    # into every span's attrs so the ingest agent_type fallback works
    # without each handler having to remember to pass it. Lazy import
    # so non-hook entry points don't pay the trace stack's cost.
    try:
        from lib.hook_plugin import set_active_agent_type
        set_active_agent_type(agent_id or agent_type)
    except Exception:
        pass
    overrides = priority_overrides(agent_type=agent_id)
    matching = sorted(
        (
            h for h in filter_enabled(handlers, agent_type=agent_id)
            if h.matches(payload)
        ),
        key=lambda h: (overrides.get(h.name, h.priority), h.name),
    )

    # Activity-log dispatcher trace. Lazy import so `python -m hook_manager
    # --help` and other fast paths don't pay the loguru cost.
    from lib.activity_log import get_activity_logger
    hooks_log = get_activity_logger("hooks").bind(
        event=payload.event, agent_type=agent_id, pid=os.getpid(),
    )

    responses: list[HookResponse] = []
    for h in matching:
        t0 = time.monotonic()
        try:
            r = h.fn(payload)
            if r is not None:
                responses.append(r)
            elapsed_ms = (time.monotonic() - t0) * 1000
            hooks_log.write("handler_dispatched",
                            handler=h.name, elapsed_ms=round(elapsed_ms, 2))
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _log_error(h.name, payload.event, exc, payload)
            hooks_log.error("handler_failed", exc_info=True,
                            handler=h.name, elapsed_ms=round(elapsed_ms, 2),
                            error_type=type(exc).__name__)
        if elapsed_ms > _SLOW_HANDLER_MS:
            _log_slow(h.name, payload.event, elapsed_ms, payload)
            hooks_log.warn("handler_slow",
                           handler=h.name, elapsed_ms=round(elapsed_ms, 2),
                           threshold_ms=_SLOW_HANDLER_MS)

    merged = merge_responses(responses)

    # Decide exit code: only use 2 for events where the spec says so.
    exit_code = merged.exit_code
    if exit_code == 2 and payload.event not in BLOCKABLE_VIA_EXIT_2:
        exit_code = 0

    out_format = getattr(payload.resolved_provider, 'hook_output_format', 'claude')
    _write_response(out_format, payload.event, merged, exit_code, stdout)
    return exit_code


def _write_response(out_format, event, merged, exit_code, stdout) -> None:
    """Emit the merged response in the active provider's hook-output dialect.

    Claude/Codex get the full JSON object on stdout. Kimi gets only its tiny
    recognized surface (or nothing), plus the block reason on stderr — printing
    Claude-only fields there would render as raw JSON in the Kimi UI.
    """
    if out_format == 'kimi':
        text = kimi_response_text(event, merged)
        if text:
            stdout.write(text + '\n')
            stdout.flush()
        if exit_code == 2:
            reason = kimi_block_reason(merged)
            if reason:
                sys.stderr.write(reason + '\n')
                sys.stderr.flush()
        return
    out_json = response_to_json(event, merged)
    stdout.write(json.dumps(out_json, ensure_ascii=False) + '\n')
    stdout.flush()


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m hook_manager <EventName> [--agent-type <type>]`."""
    from .registry import REGISTRY  # late import so tests can swap in
    args = argv if argv is not None else sys.argv[1:]
    event_hint, agent_type = _parse_args(args)
    stdin_text = sys.stdin.read()
    return run(event_hint, REGISTRY, stdin_text, sys.stdout, agent_type=agent_type)


def _parse_args(args: list[str]) -> tuple[str, str | None]:
    """Parse the tiny CLI without pulling in argparse startup overhead."""
    event_hint = ''
    agent_type: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--agent-type':
            if i + 1 < len(args):
                agent_type = args[i + 1]
                i += 2
                continue
            i += 1
            continue
        if arg.startswith('--agent-type='):
            agent_type = arg.split('=', 1)[1]
            i += 1
            continue
        if not event_hint:
            event_hint = arg
        i += 1
    return event_hint, agent_type
