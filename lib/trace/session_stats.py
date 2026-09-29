"""Engineering-time analytics over recorded sessions.

Answers "where did the time actually go?" from the span timeline, at three
nested scopes:

* **wall** — first to last event of a session; includes overnight gaps, so it
  is only useful as a denominator.
* **engaged** — sum of inter-event gaps ≤ `ENGAGED_GAP_MS`. A human watching
  an agent, reading a diff, or typing the next prompt still produces events
  within a few minutes of each other, so this is the closest proxy for
  attended time at the keyboard.
* **active** — sum of gaps ≤ `ACTIVE_GAP_MS`, the same rule
  `lib.trace.projection._compute_active_work_ms` persists on
  `sessions.active_work_ms`. This is the agent operating.

Gaps are attributed to the span that *ends* them, because tool and turn spans
are emitted after the work completes (PostToolUse fires on return), so the
interval preceding an event is the work that produced it. Summing span widths
instead would read zero for most tool spans.

Sessions run concurrently, so per-session engaged time double-counts. Use
`merge_intervals` over `engaged_windows` for real calendar time; the ratio of
the two is how many agents were running at once.

Two known divergences from the number the trace UI shows for a session:
`_compute_active_work_ms` also drops spans that have children, and it treats a
narrower set of names as containers. Counting container spans as gap endpoints
splits some >60s gaps into countable ≤60s pieces, so `active_ms` here reads
~0.6% high at the median and ~8% at p90 against the stored
`sessions.active_work_ms`. Deliberate: a gap should be charged to whatever
event ended it, including a prompt.

`human_ms` is NOT `engaged - active`. A duration cutoff cannot tell a human
apart from a slow agent: 43 h of >60 s gaps across the corpus end at
`assistant.thinking` and 17 h end at a long `tool.Bash`, none of which is a
person. Human time is therefore event-derived — the gap ending at a *user*
prompt (excluding subagent launch prompts), plus the gap after a question or
approval request the agent was blocked on. `agent_ms` is the remainder of
attended time.
"""

from __future__ import annotations

from lib.trace.session_time import (
    ACTIVE_GAP_MS,
    CONTAINER_SPANS,
    ENGAGED_GAP_MS,
    SessionStat,
    StatsFilter,
    Totals,
    _events_cte,
    _gap_expr,
    _project_of,
    _query,
    classify_command,
    engaged_windows,
    group_by,
    load_activity,
    load_commands,
    load_sessions,
    merge_intervals,
    split_by_day,
    summarize,
)
from lib.trace.session_diagnosis import (
    Claim,
    Delegation,
    Diagnosis,
    Finding,
    RAMPUP_MIN_MS,
    RAMPUP_MIN_SHARE,
    REPEAT_CMD_MIN,
    REREAD_MIN,
    REWORK_EDIT_MIN,
    Timeline,
    analyse,
    build_timeline,
    delegation,
    diagnose,
    diagnose_session,
    remainder_groups,
)
from lib.trace.session_detail import (
    SLOWEST_OPS,
    Segment,
    SessionDetail,
    _fetch_timeline,
    is_user_prompt,
    resolve_trace_id,
    session_detail,
)


