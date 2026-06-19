"""Handlers: SessionStart / SessionEnd lifecycle markers.

Emits `session` spans to the trace DB so downstream projections can graft
prompts and tool calls under the session root (see `lib/trace/projection.py`
which looks for `conversation` / session-level spans to parent orphan
prompts).

No `additional_context` emitted — the session start/end are obvious to the
model without a hook breadcrumb (silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

import os

from ..core import HookPayload, HookResponse


def handle_start(payload: HookPayload) -> HookResponse | None:
    try:
        # SessionStart's spec field is `source` (startup | resume | clear | compact).
        # `model` is also on the payload — capture it so the Sessions UI can
        # show which model each session used (and what a /model switch
        # mid-session produced on resume).
        agent_type = _session_agent_type(payload)
        _emit_span(payload, 'session.start',
                   key_aliases=(('source', 'source'), ('model', 'model')),
                   default_key='source', default_value='startup',
                   extra_attrs={'agent_type': agent_type} if agent_type else None)
    except Exception:
        pass
    try:
        _emit_git_status(payload)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


# Mirror of claude-code's MAX_STATUS_CHARS (src/context.ts): the harness
# truncates the injected status at 2k chars, so we reproduce the exact cut.
_MAX_STATUS_CHARS = 2000
_STATUS_TRUNCATION_NOTE = (
    '\n... (truncated because it exceeds 2k characters. If you need more '
    'information, run "git status" using BashTool)'
)


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _emit_git_status(payload: HookPayload) -> None:
    """Record the git-status block the harness injects into system context.

    Claude Code's `getGitStatus` (claude-code `src/context.ts`) runs git
    itself and prepends a status block to the cached system context — it
    never flows through a tool call, a hook, or the transcript, so it is
    otherwise invisible to the trace (the original "I can't find a span for
    how the agent knew about that file" gap). We reconstruct the same block
    at SessionStart, in the same cwd, moments after the harness built it.

    Caveat: this is a best-effort *reconstruction* at hook time, not the
    literal injected bytes — a file changed in the gap between the harness
    snapshot and this hook could differ. Tagged `captured_at` accordingly.
    Gated on the same env flags the harness honours, so we never record a
    block the agent never saw.
    """
    cwd = payload.cwd
    if not cwd:
        return
    # The harness skips the block in remote runs or when git instructions
    # are disabled; stay faithful to what the agent actually received.
    if _env_truthy('CLAUDE_CODE_REMOTE') or _env_truthy('CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS'):
        return

    from lib.sync.git_ops import git, GitError

    def _git(*args: str) -> str:
        try:
            return git(cwd, '--no-optional-locks', *args)
        except (GitError, FileNotFoundError, OSError):
            return ''

    # No separate is_git_repo() probe: `_git` already swallows the error a
    # non-repo cwd raises, so a fourth subprocess on every SessionStart only to
    # detect that case is wasted. If all three reads come back empty (non-repo
    # or git unavailable) there's nothing to record.
    branch = _git('rev-parse', '--abbrev-ref', 'HEAD')
    status = _git('status', '--short')
    log = _git('log', '--oneline', '-n', '5')
    if not any((branch, status, log)):
        return

    truncated = len(status) > _MAX_STATUS_CHARS
    shown_status = status[:_MAX_STATUS_CHARS] + _STATUS_TRUNCATION_NOTE if truncated else status

    # Reproduce the harness's rendered block so the trace shows verbatim
    # what was injected (sans the main-branch / git-user lines, which the
    # agent's knowledge-of-files question doesn't hinge on).
    block = '\n\n'.join([
        'This is the git status at the start of the conversation. Note that '
        'this status is a snapshot in time, and will not update during the '
        'conversation.',
        f'Current branch: {branch}',
        f'Status:\n{shown_status or "(clean)"}',
        f'Recent commits:\n{log}',
    ])

    changed_count = len([ln for ln in status.splitlines() if ln.strip()])
    _emit_span(
        payload,
        'environment.git_status',
        extra_attrs={
            'block': block,
            'branch': branch,
            'changed_count': changed_count,
            'truncated': truncated,
            'captured_at': 'session_start_hook',
        },
    )


def handle_end(payload: HookPayload) -> HookResponse | None:
    try:
        # SessionEnd's spec field is `reason` (logout | clear | prompt_input_exit | …).
        _emit_span(payload, 'session.end',
                   key_aliases=(('reason', 'reason'),))
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_stop_fallback(payload: HookPayload) -> HookResponse | None:
    """Stop→session.end fallback for providers that never emit SessionEnd.

    Gated on the provider capability `synthesizes_session_end_from_stop`
    (today only Codex) rather than a hardcoded vendor string, so a new
    provider opts in from its own adapter without editing this handler.
    Caveat: Stop is per-turn, so this may mark a long-lived interactive
    session as ended before the user truly exits.
    """
    if payload.event != 'Stop':
        return None
    if not _codex_stop_end_fallback_enabled():
        return None

    # Resolve the capability off the session's STORED agent_type (set at
    # SessionStart), not the globally configured provider. This prevents
    # incorrectly ending a Claude/Kimi session when regin happens to be
    # configured as codex.
    if not _provider_synthesizes_end_from_stop(payload.session_id):
        return None

    try:
        _emit_span(
            payload,
            'session.end',
            default_key='reason',
            default_value='stop_fallback',
            extra_attrs={
                'synthetic': True,
                'source_event': 'Stop',
            },
        )
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _codex_stop_end_fallback_enabled() -> bool:
    """Feature flag for Codex Stop->session.end synthesis.

    Defaults to enabled. Set `REGIN_CODEX_STOP_END_FALLBACK=0` to disable.
    """
    raw = (os.environ.get('REGIN_CODEX_STOP_END_FALLBACK') or '').strip().lower()
    return raw not in {'0', 'false', 'off', 'no'}


def _provider_synthesizes_end_from_stop(trace_id: str | None) -> bool:
    """True when the session's stored provider opts into the Stop→session.end
    fallback. Builds the provider from the persisted agent_type and reads its
    `synthesizes_session_end_from_stop` capability — no vendor string here."""
    agent_type = _session_agent_type_from_db(trace_id)
    if not agent_type:
        return False
    try:
        from lib.providers import build_provider, is_provider_id
        if not is_provider_id(agent_type):
            return False
        return bool(getattr(build_provider(agent_type), 'synthesizes_session_end_from_stop', False))
    except Exception:
        return False


def _session_agent_type_from_db(trace_id: str | None) -> str | None:
    """Return the stored agent_type for a session, or None if not found."""
    if not trace_id:
        return None
    try:
        from lib.orm.engine import get_connection
    except Exception:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_type FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return row['agent_type'] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _session_agent_type(payload: HookPayload) -> str | None:
    provider_id = payload.raw.get('provider_id')
    if isinstance(provider_id, str) and provider_id.strip():
        return provider_id.strip()

    raw = payload.raw.get('agent_type')
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    # Defensive fallback for legacy hook commands installed before
    # `--agent-type` was required. Delegate the model→provider inference to
    # the registry (single source of truth) rather than re-implementing the
    # per-vendor prefix table here.
    from lib.providers import provider_id_from_model
    inferred = provider_id_from_model(payload.raw.get('model'))
    if inferred:
        return inferred

    resolved_id = getattr(payload.resolved_provider, 'provider_id', None)
    return resolved_id if isinstance(resolved_id, str) and resolved_id else None


def _emit_span(
    payload: HookPayload,
    name: str,
    key_aliases: tuple[tuple[str, str], ...] = (),
    default_key: str | None = None,
    default_value: str | None = None,
    extra_attrs: dict | None = None,
) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    if payload.cwd:
        attrs['cwd'] = payload.cwd
    for raw_key, attr_key in key_aliases:
        v = payload.raw.get(raw_key)
        if v:
            attrs[attr_key] = v
    if default_key and default_key not in attrs and default_value:
        attrs[default_key] = default_value
    if extra_attrs:
        attrs.update(extra_attrs)
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
    )
