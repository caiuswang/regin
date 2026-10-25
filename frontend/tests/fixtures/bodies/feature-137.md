# Task arm: "end the session once the agent reports finished" (one_shot)

## Intent
Arming a task with `one_shot` means the run's session ends when the work is over — and the only event that says so is the agent's own accepted `regin task finish` report. It replaces the earlier "end after the first turn" semantics, which were wrong on both launch tiers.

Original report (operator): SDK-launched runs sometimes ended early, and tmux-launched runs never closed their pane even with the option on.

## Root cause (as found)
| Tier | Old behaviour | Defect |
|---|---|---|
| sdk | `one_shot` was passed through to `supervisor.launch_run`; `run_session` closed the queue at launch, so the pump ended at the **first** `ResultMessage` | A turn that ends waiting on a background shell / Monitor wake, or with a question, disconnected the child; `_settled` read `exited` as `done` → task marked done with no report |
| tmux | `tmux_runs.launch_attempt` never read `one_shot` | The tmux session lived until an operator Stop or the boot reaper |

## Design
```
params.one_shot: bool          # armed; public
params.end_requested: bool     # internal (runs._INTERNAL_PARAMS) — stamped by the accepted report
```

Session lifecycle of a one-shot task run (both tiers):
```
launched(steerable) --turn ends, no report--> parked(open)        # honest: work unreported
launched/parked --task finish accepted--> ending(after this turn)
ending --turn boundary--> ended
any --operator Stop--> ended
```

| Trigger | sdk | tmux |
|---|---|---|
| launch | always steerable (`_LAUNCH_DEFAULTS` has no `one_shot`) | unchanged |
| report accepted (`finish_run`, after the open-defect refusal and after `_settle_report`) | `run_ending.request_end` → `supervisor.end_after_turn(trace)` → `runner.close("completed")` on the run's loop; no interrupt; `_settle_stream` still waits on delegated work | `request_end` stamps `end_requested` |
| turn boundary | pump reads `_CLOSE` and stops | `Stop` hook `task_run_end_on_stop` (priority 1000, after every other Stop handler) → `run_ending.end_reported_tmux(session_id)` → `attach.release_own_session` (retire pane row, then kill `task-N-aM`) |
| report refused (409) | nothing | nothing stamped |
| run not one-shot | nothing | nothing |

## Invariants
- A one-shot task session never ends because a turn returned a result; only a report, a Stop, a crash, or (if configured) the idle timeout ends it.
- `end_requested` never appears in serialized params and never carries to a new attempt.
- The `/live` launch sheet's `one_shot` and `topics.proposal_external` keep the SDK-level "end with the first turn" meaning — out of scope here.

## Acceptance
```gherkin
Scenario: one-shot SDK task launches steerable
  Given a task armed one_shot on the sdk tier
  When it runs
  Then supervisor.launch_run receives no one_shot

Scenario: report ends a one-shot SDK run after its turn
  When the agent's task finish is accepted
  Then supervisor.end_after_turn is called once with the run's trace id

Scenario: refused report ends nothing
  Given the task fixes an open defect
  When task finish returns 409
  Then end_after_turn is not called and end_requested is not stamped

Scenario: tmux one-shot pane ends at the Stop after the report
  Given a one-shot tmux attempt
  When Stop fires before the report, then nothing is killed
  When the report is accepted and Stop fires, then task-N-aM is killed exactly once

Scenario: steerable runs are untouched
  Given a task armed without one_shot (either tier)
  When the report is accepted and Stop fires
  Then the session stays open
```
Tests: `tests/tasks/test_run_ending.py`.

## Non-goals / known gaps
- A report made from outside the agent's turn (e.g. by hand while a tmux pane is idle) ends that pane only at its next `Stop`.
- A CLI-side settle with the server down still stamps `end_requested`; the tmux pane then ends at its next Stop, the SDK session is already gone with the server.
