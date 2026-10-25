A session's status must say whether its agent is mid-turn (working) or parked at the prompt (idle) — not infer it from span age — and must not read idle while subagents/delegated work it is waiting on are still running.

Design, by tier:

- SDK tier (a run regin launched): the runner's verdict (lib/agent_sdk/registry.py run_phase, AgentRunner.live_state().busy) is working while EITHER a turn is open OR delegated background work (local_agent/local_workflow tasks tracked by _TaskLedger, retired on a terminal task_notification/task_updated) is in flight. Served as `runner_phase`; every surface (sessions list, header/live _session_phase rollup, switcher, picker busy dot) reads through it.
- Hook-traced tier (no runner): the session's own turn boundaries decide, via `turn_state_expr()` (web/blueprints/trace/sessions/turn_state.py). A turn opens on a `prompt` or `task.notification` span and closes on the Stop-path `turn` span, which the Claude hook stamps with `hook_event='Stop'` and `delegated_in_flight` (count of non-shell `background_tasks` in the Stop payload). Legacy `hook.stop_summary` also counts as a Stop. `turn_state` = idle iff the newest Stop postdates the newest opener and reported 0 delegated; working otherwise; NULL if no Stop was ever recorded. Served as `turn_state` on /api/sessions rows and the session summary; the list's SQL status CASE (`_row_status_expr`, which also drives status= / status_counts / active_count) and the client `statusOfRow` both map recent (≤10 min) + turn_state idle → idle, else recent → working, else inactive.
- Kimi's `turn` span is not a boundary (it fires a parent Stop mid-turn before each SubagentStop), so Kimi sessions keep the recency fallback.
- The /live card's `_session_phase` rollup (12s working window + screen arbitration + roster-running subagent rollup) is unchanged.

Precedence (unchanged): ended/closed > runner_phase > turn_state > recency.

Known gaps: a session with no prompt and no Stop ever recorded (start-only + a late span) still reads working within the 10-minute window; a UserPromptSubmit blocked by a hook (no Stop follows) reads working until the window lapses.
