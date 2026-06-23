"""The one place to register hook handlers.

Ordering by `priority` (lower runs earlier). Gates typically get priority
< 100 so they see unmutated input; trace handlers use > 100 so they run after.

Resilience: each handler module is imported via ``_safe_import``. If one
module's top-level imports break — most commonly during a mid-session
``lib/`` refactor that moves a module the handler still imports by the
old path — the failure is logged to ``hook-errors.jsonl`` and that
single handler degrades to a silent no-op. The other handlers, and in
particular ``post_tool_trace``, keep emitting spans. Without this guard
a single bad ``from lib import X`` in any handler module crashes the
whole registry at process start and silently blackholes every span for
the duration of the broken state.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import traceback
from datetime import datetime

from .core import Handler, always, match_bash_command, match_tool


def _log_handler_import_failure(handler_name: str, exc: BaseException) -> None:
    """Best-effort note to hook-errors.jsonl when a handler module fails
    to import. Hits the same file as runner.py's per-call errors so any
    UI/log scraper picks both up. Never raises."""
    try:
        # Resolve traces_dir lazily — importing lib.providers at module
        # top level would defeat the whole point of this resilience.
        from lib.providers import get_active_provider  # type: ignore
        path = os.path.join(str(get_active_provider().traces_dir()), 'hook-errors.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            'timestamp': datetime.now().isoformat(),
            'handler': handler_name,
            'event': 'registry_import',
            'error_type': type(exc).__name__,
            'error': str(exc),
            'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        with open(path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


class _NoOpHandlerModule:
    """Stand-in for a handler module whose import failed. Any attribute
    access returns a callable that returns None, so any Handler whose
    ``fn`` points at this stub becomes a silent no-op rather than
    raising when the dispatcher calls it."""
    def __init__(self, name: str) -> None:
        self._name = name
    def __getattr__(self, attr: str):  # noqa: D401 — stub
        def _noop(*_a, **_kw):
            return None
        _noop.__name__ = f'<{self._name}.{attr}_stub>'
        return _noop


def _safe_import(name: str):
    try:
        return importlib.import_module(f'.handlers.{name}', package='hook_manager')
    except Exception as exc:
        _log_handler_import_failure(name, exc)
        return _NoOpHandlerModule(name)


compact_lifecycle   = _safe_import('compact_lifecycle')
cwd_changed         = _safe_import('cwd_changed')
doc_check           = _safe_import('doc_check')
file_changed        = _safe_import('file_changed')
memory_recall       = _safe_import('memory_recall')
rule_check          = _safe_import('rule_check')
misc_events         = _safe_import('misc_events')
permission_events   = _safe_import('permission_events')
plan_trace          = _safe_import('plan_trace')
post_tool_failure   = _safe_import('post_tool_failure')
post_tool_trace     = _safe_import('post_tool_trace')
pre_tool_trace      = _safe_import('pre_tool_trace')
prompt_trace        = _safe_import('prompt_trace')
session_id_probe    = _safe_import('session_id_probe')
session_lifecycle   = _safe_import('session_lifecycle')
skill_invoke        = _safe_import('skill_invoke')
skill_experience    = _safe_import('skill_experience')
skill_launch        = _safe_import('skill_launch')
skill_read          = _safe_import('skill_read')
subagent_lifecycle  = _safe_import('subagent_lifecycle')
task_lifecycle      = _safe_import('task_lifecycle')
trace_payload       = _safe_import('trace_payload')
turn_trace          = _safe_import('turn_trace')


REGISTRY: list[Handler] = [
    # ── Gates (run first; can block) ───────────────────────────────────
    Handler(
        name='permission_request_pre_tool',
        label='Permission Request Details',
        summary='Surfaces provider-neutral permission request details before prompting.',
        match_hint='PreToolUse payloads that carry permission request metadata',
        events=['PreToolUse'],
        kind='gate',
        priority=20,
        fn=permission_events.handle_pre_tool_request,
    ),
    Handler(
        name='session_id_probe',
        label='Session-ID Probe',
        summary='Answers a `regin session-id` probe command by rewriting it to echo the live session id, so the agent can read its own id off stdout.',
        match_hint='PreToolUse Bash calls whose whole command is `regin session-id`',
        events=['PreToolUse'],
        kind='gate',
        priority=30,
        predicate=match_bash_command(r'^\s*regin[ -]session[ -]id\s*$'),
        fn=session_id_probe.handle,
    ),
    Handler(
        name='pre_tool_trace',
        label='Pre-Tool Pending Trace',
        summary='Emits a live PENDING span so in-flight tools (blocking + long-running like Bash/MCP) show while they run.',
        match_hint='PreToolUse for blocking + long-running tools (AskUserQuestion, ExitPlanMode, Bash, WebFetch, mcp__*)',
        events=['PreToolUse'],
        kind='trace',
        priority=100,
        fn=pre_tool_trace.handle,
    ),
    # ── Enrichers (add context / traces) ───────────────────────────────
    Handler(
        name='skill_invoke',
        label='Skill Invoke Trace',
        summary='Records when a skill is explicitly invoked via slash command.',
        match_hint='UserPromptExpansion events for slash commands',
        events=['UserPromptExpansion'],
        kind='enrich',
        priority=90,
        fn=skill_invoke.handle,
    ),
    Handler(
        name='skill_read',
        label='Skill Read Trace',
        summary='Records when Claude reads deployed skill content files.',
        match_hint='Read tool calls for .claude/skills/*/content.md',
        events=['PostToolUse'],
        kind='enrich',
        priority=100,
        predicate=match_tool('Read'),
        fn=skill_read.handle,
    ),
    Handler(
        name='skill_experience',
        label='Skill Experience Inject',
        summary='Injects <skill_experience> (memories filed under the skill) '
                'when the assistant auto-invokes a skill via the Skill tool.',
        match_hint='PreToolUse for the Skill tool',
        events=['PreToolUse'],
        kind='enrich',
        priority=110,
        predicate=match_tool('Skill'),
        fn=skill_experience.handle,
    ),
    Handler(
        name='skill_launch',
        label='Skill Launch Trace',
        summary='Records assistant-initiated Skill tool launches.',
        match_hint='PostToolUse for the Skill tool',
        events=['PostToolUse'],
        kind='enrich',
        priority=105,
        predicate=match_tool('Skill'),
        fn=skill_launch.handle,
    ),
    Handler(
        name='post_tool_trace',
        label='Tool Call Trace',
        summary='Emits a trace span for successful tool calls.',
        match_hint='All successful PostToolUse events',
        events=['PostToolUse'],
        kind='trace',
        priority=110,
        fn=post_tool_trace.handle,
    ),
    Handler(
        name='plan_trace',
        label='Plan Boundary Trace',
        summary='Tracks plan-mode boundaries and plan-file write/update attribution.',
        match_hint='ExitPlanMode or Write/Edit/MultiEdit under plans_dir',
        events=['PostToolUse'],
        kind='trace',
        priority=100,
        fn=plan_trace.handle,
    ),
    Handler(
        name='rule_check',
        label='Rule Check',
        summary='Runs applicable rule-engine checks on edited files.',
        match_hint='Edit, Write, or MultiEdit on supported source files',
        events=['PostToolUse'],
        kind='enrich',
        # Runs AFTER post_tool_trace (priority 110) so the rule.check
        # span's `start_time` lands after the `tool.Edit`/`tool.Write`
        # span emitted in the same PostToolUse pass. Otherwise it sorts
        # ~10ms earlier in the trace UI and reads as if it ran before
        # the edit.
        priority=120,
        fn=rule_check.handle,
    ),
    Handler(
        name='doc_check',
        label='Doc Hygiene Check',
        summary='Warns when a Markdown edit introduces rot-prone counts or stale phrases.',
        match_hint='Write, Edit, or MultiEdit on *.md files',
        events=['PostToolUse'],
        kind='enrich',
        priority=85,
        fn=doc_check.handle,
    ),
    Handler(
        name='memory_recall',
        label='Memory Recall Inject',
        summary='Injects <recalled_experience> from the agent-memory store into eligible prompts.',
        match_hint='Real user prompts (no slash commands / task notifications) when agent_memory.auto_inject is on',
        events=['UserPromptSubmit'],
        kind='enrich',
        priority=90,
        fn=memory_recall.handle,
    ),
    Handler(
        name='prompt_trace',
        label='Prompt Trace',
        summary='Captures lightweight prompt spans and plan-mode detection.',
        match_hint='All user prompt submissions',
        events=['UserPromptSubmit'],
        kind='trace',
        priority=100,
        fn=prompt_trace.handle,
    ),

    # ── Session lifecycle ──────────────────────────────────────────────
    Handler(
        name='session_start',
        label='Session Start',
        summary='Writes the session-start lifecycle span.',
        events=['SessionStart'],
        kind='trace',
        priority=50,
        fn=session_lifecycle.handle_start,
    ),
    Handler(
        name='session_end',
        label='Session End',
        summary='Writes the session-end lifecycle span.',
        events=['SessionEnd'],
        kind='trace',
        priority=50,
        fn=session_lifecycle.handle_end,
    ),
    Handler(
        name='session_end_from_stop',
        label='Session End (Stop Fallback)',
        summary='Codex-only fallback: synthesizes session end from Stop.',
        events=['Stop'],
        kind='trace',
        priority=55,
        fn=session_lifecycle.handle_stop_fallback,
    ),

    # ── Subagent lifecycle ─────────────────────────────────────────────
    Handler(
        name='subagent_start',
        label='Subagent Start',
        summary='Tracks subagent start lifecycle spans.',
        events=['SubagentStart'],
        kind='trace',
        priority=50,
        fn=subagent_lifecycle.handle_start,
    ),
    Handler(
        name='subagent_stop',
        label='Subagent Stop',
        summary='Tracks subagent stop lifecycle spans.',
        events=['SubagentStop'],
        kind='trace',
        priority=50,
        fn=subagent_lifecycle.handle_stop,
    ),

    # ── Compaction boundaries ──────────────────────────────────────────
    Handler(
        name='pre_compact',
        label='Pre-compact Trace',
        summary='Marks the boundary before context compaction.',
        events=['PreCompact'],
        kind='trace',
        priority=50,
        fn=compact_lifecycle.handle_pre,
    ),
    Handler(
        name='post_compact',
        label='Post-compact Trace',
        summary='Marks the boundary after context compaction.',
        events=['PostCompact'],
        kind='trace',
        priority=50,
        fn=compact_lifecycle.handle_post,
    ),

    # ── Task lifecycle ─────────────────────────────────────────────────
    Handler(
        name='task_created',
        label='Task Created',
        summary='Tracks background task creation spans.',
        events=['TaskCreated'],
        kind='trace',
        priority=50,
        fn=task_lifecycle.handle_created,
    ),
    Handler(
        name='task_completed',
        label='Task Completed',
        summary='Tracks background task completion spans.',
        events=['TaskCompleted'],
        kind='trace',
        priority=50,
        fn=task_lifecycle.handle_completed,
    ),

    # ── Per-turn token usage from the transcript ──────────────────────
    # Fires on UserPromptSubmit + SessionEnd + Stop so every assistant
    # API call eventually gets its own `turn.usage` span, not just the
    # last one before a user prompt. Stop occasionally runs before the
    # transcript has flushed the assistant message for that turn —
    # that's tolerated because the next Stop/UserPromptSubmit rescans
    # the whole file and the idempotent `usage_<uuid>` span_id means
    # no double-count. A /model switch mid-session emits no dedicated
    # hook event, so this handler is still the only path that updates
    # sessions.model across switches.
    #
    # Also fires on PreToolUse + PostToolUse via a lean fast path that
    # only ingests new `assistant_response` spans (no turn/usage/
    # tool_attribution work). This keeps the live trace UI from lagging
    # by a whole prompt cycle: the assistant text/thinking that precedes
    # a tool_use is already in the transcript by the time PreToolUse
    # fires, so it appears the moment the tool is *proposed* — crucially
    # before a permission prompt resolves (Kimi/Claude can sit minutes at
    # an approval prompt, during which only PreToolUse has fired). The
    # PostToolUse tick then catches the tool result. Throttled by the
    # per-session seen-uuid cache (turn_trace.py).
    #
    # Priority 150 so on UserPromptSubmit it runs AFTER prompt_trace
    # (priority 100). Otherwise the `turn` span's timestamp lands a
    # few microseconds BEFORE the new `prompt` span, sorts earlier in
    # `_graft_orphans`, and gets grafted under the PREVIOUS prompt —
    # widening that prompt's envelope to the next user input and
    # making its duration include the entire user-idle gap between
    # the two prompts.
    Handler(
        name='turn_trace',
        label='Turn Usage Trace',
        summary='Backfills per-turn usage spans from the transcript file.',
        match_hint='UserPromptSubmit, SessionEnd, Stop, PreToolUse, and PostToolUse events',
        events=['UserPromptSubmit', 'SessionEnd', 'Stop', 'PreToolUse', 'PostToolUse'],
        kind='trace',
        priority=150,
        fn=turn_trace.handle,
    ),

    # ── Tool-call failure + permission events ──────────────────────────
    Handler(
        name='post_tool_failure',
        label='Tool Failure Trace',
        summary='Persists failed tool-call spans with the error shape.',
        events=['PostToolUseFailure'],
        kind='trace',
        priority=50,
        fn=post_tool_failure.handle,
    ),
    Handler(
        name='permission_request',
        label='Permission Request',
        summary='Traces Claude permission request events.',
        events=['PermissionRequest'],
        kind='trace',
        priority=50,
        fn=permission_events.handle_request,
    ),
    Handler(
        name='permission_denied',
        label='Permission Denied',
        summary='Traces permission denial events.',
        events=['PermissionDenied'],
        kind='trace',
        priority=50,
        fn=permission_events.handle_denied,
    ),

    # ── Environment / filesystem events ────────────────────────────────
    Handler(
        name='file_changed',
        label='File Changed',
        summary='Records watched file-change events from Claude Code.',
        events=['FileChanged'],
        kind='trace',
        priority=50,
        fn=file_changed.handle,
    ),
    Handler(
        name='cwd_changed',
        label='Directory Changed',
        summary='Records working-directory changes.',
        events=['CwdChanged'],
        kind='trace',
        priority=50,
        fn=cwd_changed.handle,
    ),

    # ── Miscellaneous logging-only events ──────────────────────────────
    Handler(
        name='teammate_idle',
        label='Teammate Idle',
        summary='Logs teammate-idle notifications into the trace stream.',
        events=['TeammateIdle'],
        kind='trace',
        priority=50,
        fn=misc_events.teammate_idle,
    ),
    Handler(
        name='instructions_loaded',
        label='Instructions Loaded',
        summary='Logs instruction reload events.',
        events=['InstructionsLoaded'],
        kind='trace',
        priority=50,
        fn=misc_events.instructions_loaded,
    ),
    Handler(
        name='config_change',
        label='Config Change',
        summary='Logs Claude config-change events.',
        events=['ConfigChange'],
        kind='trace',
        priority=50,
        fn=misc_events.config_change,
    ),
    # WorktreeCreate/WorktreeRemove are DEPRECATED for hook_manager.
    # The harness reads the WorktreeCreate hook's stdout as the new worktree's
    # path; hook_manager's default `{"suppressOutput": true}` response gets
    # parsed as a path and breaks EnterWorktree (chdir ENOENT). The runner
    # cannot tell which event response shape applies without per-event opt-out
    # logic, so the safest fix is to leave both events unwired and let the
    # Claude Code harness use its built-in git-worktree path.
    # The handler below is kept commented for reference; do not re-register.
    # Handler(
    #     name='worktree_remove',
    #     label='Worktree Removed',
    #     summary='Logs worktree removal events.',
    #     events=['WorktreeRemove'],
    #     kind='trace',
    #     priority=50,
    #     fn=misc_events.worktree_remove,
    # ),

    # ── Catch-all trace (runs last) ────────────────────────────────────
    Handler(
        name='trace_payload',
        label='Raw Payload Trace',
        summary='Appends every hook payload to the local JSONL debug log.',
        match_hint='Catch-all fallback for every event',
        events=['*'],
        kind='trace',
        priority=900,
        predicate=always(),
        fn=trace_payload.handle,
    ),
]


def _load_custom_handlers(module_name: str = 'hook_manager.custom_registry') -> list[Handler]:
    """Import `module_name` and return its `CUSTOM_HANDLERS` list.

    Degrades gracefully:
      - Module not present → empty list, silent.
      - Module present but has no `CUSTOM_HANDLERS` attr → empty list, silent.
      - Module raises on import → empty list, warning to stderr.

    Separated out so tests can pass a nonsense module name without having
    to monkey-patch Python's import machinery.
    """
    import importlib
    import sys

    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        return []
    except Exception as exc:
        sys.stderr.write(
            f'[hook_manager] custom_registry failed to load: {exc!r}\n'
        )
        return []

    handlers = getattr(mod, 'CUSTOM_HANDLERS', None)
    if not isinstance(handlers, list):
        return []
    return handlers


REGISTRY.extend(_load_custom_handlers())


def describe_handlers(
    routed_events: set[str] | None = None,
    agent_type: str | None = None,
) -> list[dict]:
    """Return a JSON-friendly snapshot of every registered handler.

    Used by the web UI to render toggles + drag-reorder. `enabled` and
    `priority` reflect the persisted config at the moment of the call;
    `default_priority` is the registry-defined value (useful for "reset"
    UX). Changes to the config file propagate on the next call.
    """
    from .config import disabled_set, priority_overrides
    disabled = disabled_set(agent_type)
    overrides = priority_overrides(agent_type)
    routed_events = routed_events or set()

    def _humanize(name: str) -> str:
        return re.sub(r'[_-]+', ' ', name).strip().title()

    def _wired_events(events: list[str]) -> list[str]:
        if '*' in events:
            return sorted(routed_events)
        return [event for event in events if event in routed_events]

    return [
        {
            'name': h.name,
            'label': h.label or _humanize(h.name),
            'summary': h.summary,
            'match_hint': h.match_hint,
            'events': list(h.events),
            'wired_events': _wired_events(h.events),
            'wired': bool(_wired_events(h.events)),
            'kind': h.kind,
            # `priority` is what the runner actually uses (override-aware) so
            # existing UI that reads h.priority keeps working without changes.
            # `default_priority` exposes the registry value so the UI can show
            # "priority 30 · default 150" + offer a reset button.
            'priority': overrides.get(h.name, h.priority),
            'default_priority': h.priority,
            'priority_overridden': h.name in overrides,
            'enabled': h.name not in disabled,
        }
        for h in REGISTRY
    ]
