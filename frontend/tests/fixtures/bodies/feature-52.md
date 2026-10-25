Sessions should carry a meaningful title and a feature tag automatically,
so this feature is now mostly a backstop for what auto-titling misses, not
a routine to run per batch of sessions.

Two automatic paths now cover the common causes of a meaningless title:

1. **Bare skill-invocation titles.** A task armed with a skill puts the
   `/skill args` line first in the prompt (`lib/tasks/runs.py::_invocation`);
   taking that literal first line as the session title collapsed every such
   session's title to the skill name. `lib/trace/trace_service/ingest.py`'s
   `_handle_prompt_title` now skips a bare slash-invocation line (via
   `_skip_slash_invocation`) when content follows it, and titles from the
   real instruction underneath instead. Only the `first_prompt` precedence
   level is affected — a real `/rename` or Claude's `ai-title` still wins.

2. **Feature tags on task-spawned sessions.** `lib/tasks/runs.py::start_run`
   now writes a `feature-<id>` `SessionTag` (source='auto') at launch, next
   to the existing `feature_events.on_run_start` call, for any task with a
   `feature_id`. Written directly (not via the `regin-tags:` prompt-marker
   path — that marker must be the prompt's literal first line, which the
   skill invocation already occupies) since `session_tags` has no FK to
   `sessions`, so the tag lands regardless of ingestion order.

What is still manual: a session's title is still not regenerated from its
actual content (defect number, feature slice) — only the bare-invocation
noise is stripped. A prompt that is genuinely just "/skill" with nothing
after it, or a session with no task/feature link at all, still gets the
generic fallback and would need the same manual `regin trace dump` +
direct-ORM-write pass this feature originally automated by hand. Rerun that
pass only for sessions neither path catches.
