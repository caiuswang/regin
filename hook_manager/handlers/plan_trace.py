"""Handler: PostToolUse → plan-file association spans + PlanSession rows.

Two ways a session can touch a plan file, both attributable:

1. `ExitPlanMode` carries plan text (Codex flow). We persist it to the
   provider's `plans_dir()` under a deterministic name that embeds the
   session prefix, so the filename alone identifies the author. Claude
   Code's `ExitPlanMode` payload has no plan text — we emit a bare
   `plan.exit` boundary marker and make no attribution claim.
2. The agent edits a plan file directly via `Write`/`Edit`/`MultiEdit`.
   When the edited `file_path` lives inside `plans_dir()`, we know
   exactly which session touched which plan.

In both attributable cases we emit a `plan.write` (Write/ExitPlanMode-
new) or `plan.update` (Edit/MultiEdit) span tagged with `plan_filename`
AND POST an `enter` event to `/api/plan-sessions` so the durable
session→plan mapping lands in the `plan_sessions` table (which is what
`queries.py:80-112` reads when joining sessions with their plan).

The existing `plan.exit` span is unchanged.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from ..core import HookPayload, HookResponse


_PLAN_WRITE_TOOLS = {'Write', 'Edit', 'MultiEdit'}


def _extract_plan_text(payload: HookPayload) -> str | None:
    """Return plan markdown from the payload, or None."""
    for candidate in (
        (payload.tool_input or {}).get('plan'),
        (payload.tool_input or {}).get('content'),
        (payload.raw or {}).get('plan'),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _write_plan_file(plan_text: str, payload: HookPayload) -> str | None:
    """Persist plan text to the provider's plans_dir with a deterministic name.

    Returns the basename of the written file, or None on failure.
    Does not overwrite an existing file (idempotent retries).
    """
    provider = payload.resolved_provider
    plans_dir = provider.plans_dir()
    try:
        plans_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    session_id = payload.session_id or 'unknown'
    session_prefix = session_id.split('-')[0] if '-' in session_id else session_id
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = f"{provider.provider_id}-plan-{session_prefix}-{timestamp}.md"
    filepath = plans_dir / filename

    if filepath.exists():
        return filename

    try:
        filepath.write_text(plan_text, encoding='utf-8')
    except OSError:
        return None
    return filename


def _file_under_plans_dir(file_path: str, plans_dir: Path | str) -> bool:
    """True if `file_path` is a file inside `plans_dir` at any depth.

    Resolves both paths to absolute form so a relative `file_path` and a
    `plans_dir` that's a `Path` object compare correctly. The strict
    `+ os.sep` suffix prevents `/tmp/plans-other/foo.md` from matching
    `/tmp/plans`.
    """
    if not file_path:
        return False
    try:
        fp = os.path.abspath(file_path)
        pd = os.path.abspath(str(plans_dir))
    except (OSError, ValueError):
        return False
    return fp.startswith(pd + os.sep)


def _plan_file_path(payload: HookPayload) -> str | None:
    ti = payload.tool_input or {}
    fp = ti.get('file_path') or ti.get('path')
    if isinstance(fp, str) and fp:
        return fp
    return None


def handle(payload: HookPayload) -> HookResponse | None:
    tool = payload.tool_name
    if tool == 'ExitPlanMode':
        try:
            plan_text = _extract_plan_text(payload)
            written_name = None
            if plan_text:
                written_name = _write_plan_file(plan_text, payload)
            # When we wrote the file this hook, tag the exit span with it.
            # Claude Code's payload has no plan text → bare plan.exit
            # boundary marker, no attribution claim.
            _emit_exit_span(payload, plan_name=written_name)
            if written_name:
                _emit_plan_op_span(payload, written_name, op='write',
                                   tool_name='ExitPlanMode')
                _post_plan_session_enter(payload, written_name)
        except Exception:
            pass
        return HookResponse(suppress_output=True)

    if tool in _PLAN_WRITE_TOOLS:
        try:
            file_path = _plan_file_path(payload)
            if not file_path:
                return None
            plans_dir = payload.resolved_provider.plans_dir()
            if not _file_under_plans_dir(file_path, plans_dir):
                return None
            plan_filename = os.path.basename(file_path)
            op = 'write' if tool == 'Write' else 'update'
            _emit_plan_op_span(payload, plan_filename, op=op,
                               file_path=file_path, tool_name=tool)
            _post_plan_session_enter(payload, plan_filename)
        except Exception:
            pass
        return None

    return None


def _emit_exit_span(payload: HookPayload, plan_name: str | None = None) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    if plan_name:
        attrs['plan_name'] = plan_name
    post_span(
        trace_id=payload.session_id,
        name='plan.exit',
        attributes=attrs,
    )


def _emit_plan_op_span(payload: HookPayload, plan_filename: str, op: str,
                       file_path: str | None = None,
                       tool_name: str | None = None) -> None:
    """Emit a plan.write / plan.update span tying this session to a plan file.

    The span is the source of truth for session→plan attribution; the
    PlanSession row that follows is a read-optimized cache for the
    sessions list query.
    """
    from lib.hook_plugin import post_span  # type: ignore
    span_name = 'plan.write' if op == 'write' else 'plan.update'
    attrs: dict = {
        'plan_filename': plan_filename,
        'op': op,
    }
    if file_path:
        attrs['file_path'] = file_path
    if tool_name:
        attrs['tool_name'] = tool_name
    post_span(
        trace_id=payload.session_id,
        name=span_name,
        attributes=attrs,
    )


def _post_plan_session_enter(payload: HookPayload, plan_filename: str) -> None:
    """Record (session_id, plan_filename) in the plan_sessions table.

    The endpoint dedupes on (session_id, plan_filename), so multiple
    edits to the same plan collapse to a single row whose `started_at`
    captures the first touch.
    """
    from lib.hook_plugin import post_event  # type: ignore
    if not payload.session_id or not plan_filename:
        return
    post_event('plan_sessions', {
        'event': 'enter',
        'session_id': payload.session_id,
        'plan_filename': plan_filename,
        'started_at': datetime.now().isoformat(),
    })
