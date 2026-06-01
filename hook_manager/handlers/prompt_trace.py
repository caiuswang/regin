"""Handler: UserPromptSubmit → plan-decision detection + task-notification spans.

Replaces the external user_prompt_trace_hook.py without touching the legacy
trace-ingest API. When a plan is active (inferred from the presence of a
recent `~/.claude/plans/*.md` file modified in the last hour), this handler
classifies the prompt as approved/rejected/neither based on keyword match
so downstream dashboards can attribute outcomes.

For real user prompts this handler emits only a live PENDING *placeholder*
(`promptlive-<hash>`), never a real anchor: at UserPromptSubmit time the
current prompt isn't yet flushed to the transcript, so a span_id derived
from it would key onto the *previous* prompt's entry and (via INSERT OR
REPLACE) clobber that prompt's anchor. The authoritative
`prompt-<prompt_uuid>` anchor (and its `prompt_images`) is owned by
`turn_trace`; `ingest_session_spans` deletes the placeholder when that
anchor lands (matched by text hash — see `lib/trace/pending_spans.py`).
"""

from __future__ import annotations

import os
import re
import time

from lib.providers import get_active_provider

from ..core import HookPayload, HookResponse

_APPROVE_WORDS = ('approve', 'proceed', 'yes', 'looks good', 'looks great',
                  'go ahead', 'continue', 'lgtm', 'ship it')
_REJECT_WORDS = ('reject', 'cancel', 'abort', 'discard', 'no thanks', 'stop')

def _detect_decision(prompt: str) -> str | None:
    lower = prompt.lower()
    for kw in _APPROVE_WORDS:
        if kw in lower:
            return 'approved'
    for kw in _REJECT_WORDS:
        if kw in lower:
            return 'rejected'
    return None


def _plan_is_active(provider=None, fresh_seconds: int = 3600) -> bool:
    """True if any plan file has been modified in the last `fresh_seconds`.

    `provider` defaults to the globally active provider so tests can
    monkey-patch `get_active_provider` or pass a provider directly.
    """
    if provider is None:
        provider = get_active_provider()
    plans_dir = str(provider.plans_dir())
    if not os.path.isdir(plans_dir):
        return False
    now = time.time()
    try:
        for fname in os.listdir(plans_dir):
            if not fname.endswith('.md'):
                continue
            try:
                if now - os.stat(os.path.join(plans_dir, fname)).st_mtime < fresh_seconds:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


_TASK_NOTIFICATION_RE = re.compile(r'<task-notification>(.*?)</task-notification>', re.DOTALL)
_TASK_FIELD_RE = {
    'task_id': re.compile(r'<task-id>([^<]*)</task-id>'),
    'tool_use_id': re.compile(r'<tool-use-id>([^<]*)</tool-use-id>'),
    'output_file': re.compile(r'<output-file>([^<]*)</output-file>'),
    'status': re.compile(r'<status>([^<]*)</status>'),
    'summary': re.compile(r'<summary>([^<]*)</summary>'),
}


def _parse_task_notification(text: str) -> dict | None:
    """Return parsed fields from a task-notification block, or None if not one.

    Claude Code injects background-task completions as synthetic user prompts
    wrapping the payload in `<task-notification>` XML tags. We want these to
    surface as their own span type rather than masquerade as user prompts.
    """
    if '<task-notification>' not in text:
        return None
    body_match = _TASK_NOTIFICATION_RE.search(text)
    if not body_match:
        return None
    body = body_match.group(1)
    fields: dict[str, str] = {}
    for key, regex in _TASK_FIELD_RE.items():
        m = regex.search(body)
        if m:
            fields[key] = m.group(1).strip()
    return fields


def handle(payload: HookPayload) -> HookResponse | None:
    text = payload.prompt
    if not text:
        return None

    task_fields = _parse_task_notification(text)

    # Background-task notifications are system-injected, not user prompts.
    # They get a dedicated `task.notification` span (best-effort) and skip
    # plan-decision keyword detection so a "completed" status doesn't get
    # misread as approval. Real user prompts intentionally get no span
    # here — `turn_trace` owns the `prompt-<prompt_uuid>` anchor (see the
    # module docstring).
    if task_fields is not None:
        try:
            _emit_task_notification_span(payload, text, task_fields)
        except Exception:
            pass
        return HookResponse(suppress_output=True)

    # Real prompt: emit a live PENDING placeholder so the trace shows the
    # prompt instantly. It's keyed off a content hash (`promptlive-…`), never
    # a real `prompt-<uuid>` anchor id, so it can't clobber turn_trace's
    # authoritative anchor; ingest deletes it the moment that anchor lands.
    try:
        _emit_placeholder(payload, text)
    except Exception:
        pass

    # Only emit additional_context when we have an actionable signal.
    # Length, slash-command detection, etc. are all things the model can
    # derive from the prompt it already received.
    if _plan_is_active(provider=payload.resolved_provider):
        decision = _detect_decision(text)
        if decision:
            return HookResponse(
                suppress_output=True,
                additional_context=f'plan_decision={decision}',
            )
    return HookResponse(suppress_output=True)


def _emit_placeholder(payload: HookPayload, text: str) -> None:
    """Emit the live `promptlive-<hash>` PENDING placeholder for a real prompt.

    No images: the authoritative anchor (and its `prompt_images`) come from
    `turn_trace`. The store is append-only, so this placeholder coexists with
    its real `prompt-<uuid>` anchor; the serve-time merge drops it (matched by
    text hash) once the anchor lands — see lib/trace/merge.py."""
    from lib.hook_plugin import post_span  # type: ignore
    from lib.trace.pending_spans import prompt_placeholder_id  # type: ignore

    attributes = {
        'text': text,
        'chars': len(text),
        'slash_command': text.split()[0] if text.startswith('/') else None,
        'live_placeholder': True,
    }
    post_span(
        trace_id=payload.session_id,
        name='prompt',
        span_id=prompt_placeholder_id(payload.session_id, text),
        attributes=attributes,
        status_code='PENDING',
    )


def _emit_task_notification_span(payload: HookPayload, text: str, fields: dict) -> None:
    """Emit a `task.notification` span for a background-task completion.

    span_id is derived from the task_id so transcript replays upsert the
    same row rather than duplicating. Falls through to a random uuid if
    the task_id is missing (best-effort idempotency).
    """
    from lib.hook_plugin import post_span  # type: ignore
    task_id = fields.get('task_id') or ''
    span_id = f'task-{task_id[:13]}' if task_id else None
    attributes = {
        'text': text,
        'task_id': fields.get('task_id'),
        'tool_use_id': fields.get('tool_use_id'),
        'output_file': fields.get('output_file'),
        'status': fields.get('status'),
        'summary': fields.get('summary'),
    }
    post_span(
        trace_id=payload.session_id,
        name='task.notification',
        span_id=span_id,
        attributes=attributes,
    )

