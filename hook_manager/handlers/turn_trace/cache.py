"""Per-session disk caches for turn_trace.

Two file artefacts live under `~/.local/share/regin/turn_trace_state/`
(or the dir named by `REGIN_TURN_TRACE_STATE_DIR` for test isolation):

  * `<trace_id>.txt`     — one turn_uuid (or attachment uuid / system
                           event uuid / local-command uuid) per line.
                           The "we already posted a span for this row"
                           cache; gates the PostToolUse fast path.
  * `<trace_id>.aititle` — last-emitted `{source}:{text}` cache key for
                           the session.title span; suppresses a re-emit
                           when neither side changed.

Server-side dedup keys (`resp-<uuid[:13]>`, etc.) make repeated posts
safe — these caches are the client-side throttle that keeps PostToolUse
from spamming a fresh HTTP call on every tool invocation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _state_dir() -> Path:
    """Where per-session seen-uuid caches live. Honours the
    REGIN_TURN_TRACE_STATE_DIR env var so tests can isolate state."""
    override = os.environ.get('REGIN_TURN_TRACE_STATE_DIR')
    if override:
        return Path(override)
    return Path.home() / '.local' / 'share' / 'regin' / 'turn_trace_state'


def _cache_path(trace_id: str) -> Path:
    return _state_dir() / f'{trace_id}.txt'


def _load_seen(trace_id: str) -> set[str]:
    p = _cache_path(trace_id)
    try:
        with open(p) as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def _mark_seen(trace_id: str, uuids: list[str]) -> None:
    if not uuids:
        return
    p = _cache_path(trace_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'a') as f:
            for u in uuids:
                f.write(u + '\n')
    except OSError:
        pass


def _ai_title_cache_path(trace_id: str) -> Path:
    return _state_dir() / f'{trace_id}.aititle'


def _load_ai_title(trace_id: str) -> str | None:
    try:
        return _ai_title_cache_path(trace_id).read_text().strip() or None
    except OSError:
        return None


def _save_ai_title(trace_id: str, title: str) -> None:
    p = _ai_title_cache_path(trace_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(title)
    except OSError:
        pass


def _looks_like_rename_line(raw: bytes) -> bool:
    # Real /rename invocations land as a local_command system entry on
    # a single line — require both markers together so an assistant
    # `Bash` tool_use that merely mentions `/rename` (greps during
    # debugging) doesn't trip the detector.
    return (
        b'"subtype":"local_command"' in raw
        and b'<command-name>/rename</command-name>' in raw
    )


def _extract_title(entry: dict) -> tuple[str | None, str | None]:
    """Return (ai_title, custom_title) — both stripped, either may be None."""
    t = entry.get('type')
    if t == 'ai-title':
        val = entry.get('aiTitle')
        if isinstance(val, str) and val.strip():
            return val.strip(), None
    elif t == 'custom-title':
        val = entry.get('customTitle')
        if isinstance(val, str) and val.strip():
            return None, val.strip()
    return None, None


def _scan_title_state(handle) -> tuple[str | None, str | None, bool]:
    """Walk the transcript once and return the title state.

    Substring-prefilter on `-title"` skips json-parsing the bulk of a
    multi-MB transcript; the `/rename` check is a cheap raw substring
    test that runs on every line.
    """
    last_ai: str | None = None
    last_custom: str | None = None
    has_rename: bool = False
    for raw in handle:
        if not has_rename and _looks_like_rename_line(raw):
            has_rename = True
        if b'-title"' not in raw:
            continue
        try:
            d = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        ai, custom = _extract_title(d)
        if ai:
            last_ai = ai
        if custom:
            last_custom = custom
    return last_ai, last_custom, has_rename


def _read_session_title(transcript_path: str) -> tuple[str | None, str | None]:
    """Return the title Claude Code shows for this session and its source.

    Claude Code writes two title-bearing line types into the JSONL:
      * `{"type":"ai-title","aiTitle":"..."}`     — auto-generated
      * `{"type":"custom-title","customTitle":"..."}` — written by /rename

    A `custom-title` only counts as a real user rename when this
    transcript also contains the originating `/rename` slash-command
    entry. Without that, the `custom-title` line was inherited from a
    parent conversation via `/clear` (or `/compact`): Claude Code copies
    the prior title forward into the new transcript and re-stamps it
    periodically even though the user never renamed this session.
    Treating the inherited copy as `user_rename` would pin every spawned
    session to its parent's name and lock out the fresh `ai-title`
    Claude later generates.

    Returns `(text, source)` where source is `'user_rename'` or
    `'claude_ai_title'`, or `(None, None)` if neither applies.
    """
    if not isinstance(transcript_path, str) or not os.path.isfile(transcript_path):
        return None, None
    try:
        with open(transcript_path, 'rb') as f:
            last_ai, last_custom, has_rename = _scan_title_state(f)
    except OSError:
        return None, None
    if last_custom and has_rename:
        return last_custom, 'user_rename'
    if last_ai:
        return last_ai, 'claude_ai_title'
    return None, None
