"""Handler: FileChanged — react to watched file edits on disk.

Two behaviors:
  (1) If a .env/.envrc file changed, re-export variables into CLAUDE_ENV_FILE
      so the session picks up the new values (no restart needed).
  (2) Always surface a tiny context breadcrumb so the model knows a change
      happened (useful when the user is editing config in another window).

The .env re-export is best-effort; parsing is deliberately simple (only
`KEY=value` lines, no multi-line strings, no variable expansion).
"""

from __future__ import annotations

import os

from ..core import HookPayload, HookResponse


def _is_env_file(path: str) -> bool:
    base = os.path.basename(path)
    return base in ('.env', '.envrc') or base.startswith('.env.')


def _reexport_env(path: str) -> int:
    """Append KEY=VAL lines to $CLAUDE_ENV_FILE, returning the count reloaded."""
    target = os.environ.get('CLAUDE_ENV_FILE')
    if not target:
        return 0
    count = 0
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if not key.isidentifier():
                    continue
                with open(target, 'a') as out:
                    out.write(f'export {key}={val}\n')
                count += 1
    except OSError:
        return count
    return count


def handle(payload: HookPayload) -> HookResponse | None:
    path = (payload.raw.get('file_path') or '').strip()
    if not path:
        return None
    base = os.path.basename(path)
    is_env = _is_env_file(path)
    reexported = _reexport_env(path) if is_env else 0

    try:
        _emit_span(payload, path, base, is_env, reexported)
    except Exception:
        pass

    if is_env:
        # Env re-export IS useful — tells the model new env vars are in play.
        return HookResponse(
            suppress_output=True,
            additional_context=f'file-changed: env file {base!r} re-exported {reexported} var(s)',
        )

    # Generic file changes: silent trace. The model doesn't need a
    # transcript breadcrumb every time a watched file is touched, but the
    # span still records it for the trace dashboard.
    return HookResponse(suppress_output=True)


def _emit_span(payload: HookPayload, path: str, base: str,
               is_env: bool, reexported: int) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs = {
        'file_path': path,
        'basename': base,
        'is_env_file': is_env,
    }
    if is_env:
        attrs['reexported_vars'] = reexported
    post_span(
        trace_id=payload.session_id,
        name='file.changed',
        attributes=attrs,
    )
