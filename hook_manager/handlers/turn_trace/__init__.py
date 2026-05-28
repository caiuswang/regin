"""Handler: UserPromptSubmit + SessionEnd + Stop + PostToolUse → emit a
`turn` span with the model used in the most recent completed turn, plus
a `turn.usage` event summarising token consumption across the whole
transcript so far, plus an `assistant_response` span per turn that
carried text.

Motivation: Claude Code does not fire any hook event when the user runs
`/model` mid-session. The SessionStart payload carries `model` but that
value is frozen at startup — a /model switch leaves the Sessions
dashboard showing the wrong model.

The transcript file at `~/.claude/projects/<cwd-munged>/<session_id>.jsonl`
records the model + usage per assistant turn. We scan it and emit:

  * a `turn` span with the latest assistant entry's `model`
  * a `turn.usage` event carrying input/output/cache-read/cache-creation
    token sums and the peak per-turn `context_used` (= prompt size sent
    to the model on the largest turn). The Sessions aggregator lifts
    these into `sessions.peak_context_tokens` etc. so the UI can show a
    "ctx: %" badge mirroring the Claude Code statusline.

PostToolUse path: the full handler runs on UserPromptSubmit / SessionEnd
/ Stop. PostToolUse fires after every tool call — by that time the
assistant entry that emitted the tool_use is already in the transcript,
so we use that event to ingest only newly-written assistant_response
turns (lean fast path). Without this the trace UI lagged by a whole
prompt cycle.

Per-session caches under `~/.local/share/regin/turn_trace_state/` track
which uuids this process tree has already posted (`.txt` file) and the
last-emitted session.title cache key (`.aititle` file). The PostToolUse
fast path consults the cache and skips re-posting; the full path also
writes to it so the two modes converge. Span IDs (`resp-<uuid[:13]>`)
are still idempotent server-side, so a missing/corrupt cache merely
means redundant upserts — never a wrong write.

Best-effort: any I/O failure on the transcript or cache is swallowed
and no span is emitted. The hook always returns
`HookResponse(suppress_output=True)` so the dispatch pipeline is never
blocked.

Package layout (this module is the public surface):

  * `entry.py`          — `handle`, `_emit_span`,
                          `_emit_assistant_response_only`,
                          `_latest_turn_model`
  * `span_posters.py`   — `_post_live_turn_data`, plus the
                          attachment / system-event / local-command
                          / server-tool / deny / error emitters
  * `deny_detection.py` — sentinel matchers + synth-span attribute
                          builders for denies and tool_use_errors
  * `cache.py`          — `_load_seen`, `_mark_seen`, `_load_ai_title`,
                          `_save_ai_title`, `_read_session_title`
  * `timestamps.py`     — `_normalise_attachment_ts`, `_to_naive_datetime`

External code accesses this package via `from hook_manager.handlers
import turn_trace`, then `turn_trace.handle(...)` /
`turn_trace._post_live_turn_data(...)` /
`turn_trace._is_permission_deny(...)` / `turn_trace._is_tool_use_error(...)`.
Those four symbols are re-exported below.
"""

from __future__ import annotations

from .deny_detection import _is_permission_deny, _is_tool_use_error
from .entry import (
    _emit_assistant_response_only,
    _emit_span,
    _latest_turn_model,
    handle,
)
from .span_posters import _post_live_turn_data

__all__ = [
    'handle',
    '_emit_span',
    '_emit_assistant_response_only',
    '_latest_turn_model',
    '_post_live_turn_data',
    '_is_permission_deny',
    '_is_tool_use_error',
]
