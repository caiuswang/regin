# Support background shell detecting

A `Bash` call made with `run_in_background: true` (or auto-backgrounded by the
harness) hands control back in milliseconds and keeps running out of sight. In
the trace it read as a 3 ms success with no output, while the completion that
arrived much later landed as an unrelated `task.notification` span. Nobody
reading the session could tell a background shell ran, whether it was still
running, or how it ended.

This makes the background shell a first-class, legible thing in the trace, for
both tiers — a `claude` the operator started in a terminal and one regin
launched over the SDK. Nothing depends on tmux, a pane, or a live process: the
record is built from hook events and the transcript, which both tiers produce.

## Non-goals

- **Terminating a shell.** regin holds no pid and no handle; only the harness
  does (`KillShell`). Finding the process by matching its command string would
  be a guess that cannot tell two identical shells apart. Reaching the agent
  over `agent_bridge` to ask it to kill one is possible but asynchronous,
  unguaranteed, and tier-dependent — deliberately not built.
- **Rewriting the span's `duration_ms`.** 3 ms is the true cost of the *tool
  call* and feeds cost/stats aggregates; the shell's elapsed time is a separate
  fact carried as its own attribute.
- `BashOutput` / `KillShell` cards.

## Type

    BackgroundShell =
      { task_id      : str            # harness id; the identity, never inferred
        tool_use_id  : str            # the launching Bash call
        command      : str
        description  : str?           # the model's own one-line gloss
        started_at   : ts
        status       : Status
        ended_at     : ts?
        elapsed_ms   : int?           # ended_at − started_at, when settled
        summary      : str?           # the harness's completion gloss
        output_file  : path? }

    Status = running | completed | failed | killed | stopped
           | <whatever <status> said>

`stopped` is a launch nothing ever reported the end of, on a session that is
over. It is not a synonym for `running`: a shell is a child of the process that
launched it, so once that process is gone the shell is too.

## State machine

    (none) ──PreToolUse, tool_input.run_in_background──▶ declared
    declared ──PostToolUse, tool_response.background_task_id──▶ running
    running ──task.notification naming it──▶ <that envelope's status>
    running ──terminal marker in its own output file──▶ killed | completed | failed
    running ──session ends with no notification──▶ stopped

## Two surfaces, two different questions

| Surface | Question | Scope |
|---|---|---|
| the launching `Bash` card | how did *this call* turn out | one page of spans |
| the foot-of-feed block | what is running *right now* | whole session |

The block cannot be derived from the card's data. `merge.py` only ever sees one
window, and a shell running for half an hour left that window long ago — which
is precisely the shell worth surfacing. So the block reads a server-owned
roster (`lib/trace/background_shells.py`), the shell equivalent of
`agent_roster`. It lists only running shells, is collapsible, and expands one
shell at a time onto the tail of its output file.

## Where each fact comes from

| Fact | Source | Written by |
|---|---|---|
| `run_in_background`, `description` | `tool_input` | PreToolUse + PostToolUse |
| `background_task_id` | `tool_response` | PostToolUse |
| status, summary, output_file | `<task-notification>` | UserPromptSubmit hook **and** the transcript backfill |
| the death of a shell nobody was told about | the `[killed]` / `[exited with code N]` line in the shell's own output file | `repair.backfill_killed_shell_notifications`, from the live rescan |
| launch↔completion link on the card | serve-time join | `lib/trace/merge.py` |
| what is running now | whole-session aggregate | `background_shells.shell_roster` |

**The notification needs three writers.** One envelope can settle several tasks
(that is how a resumed session learns its predecessor's shells were killed),
and such an envelope arrives as a *queued* item, which fires no prompt hook. A
single writer therefore lost the fact permanently. The transcript holds that
entry regardless, so it is the second writer — the same pairing every other
span family has.

A shell the harness *kills* has neither: no envelope and no transcript entry,
which is why only 357 of 1126 launched shells ever settled and two dead ones
sat in the block counting upward. Its death is recorded in exactly one place —
a `[killed]` or `[exited with code N]` line the harness appends to the shell's
own output file — so reading that marker is the third writer. It runs after
the transcript writer and is not gated on the transcript changing, since a
killed shell may produce no further entry to trigger on. The marker must be a
whole line; a live shell whose last line is exactly the marker is the one
ambiguity the format leaves. Its reach is bounded by the directory problem
below: a session no notification has ever named a directory for cannot locate
its shells' files, and settles nothing.

All three derive the span id from one shared rule, so they upsert one row and
a real envelope arriving later replaces a marker-derived one.

**Output files.** The harness states the running shell's path only in the
tool_result prose, which no hook sees, and the directory is keyed by the PARENT
session id — so the path can be neither read off the launch nor rebuilt from
this session's id (the mistake `lib/session_probe.py` warns about). What is
trustworthy is a path a completion notification states outright: every task of
one session writes into the same directory, so one observed `output_file`
locates the rest, and a file is used only after its name is confirmed to be the
task's own. The newest such statement wins — a resume replaces the directory
mid-trace. A session that has stated no directory yet reports "no readable
output", which is a gap, not a guess.

## Invariants

- Every join key is an identifier equality (`background_task_id == task_id`).
  No timestamp, ordering, or proximity test decides a match.
- `merge_spans` stays pure and idempotent.
- A `task.notification` matching no Bash span — every subagent report — is
  untouched and still rendered as its own card.
- `duration_ms` on the Bash span is never rewritten.
- A span the join enriched names the row it derived from (`derived_from_id`).
  The read path withholds a body the client already holds once it is below the
  watermark and settled — a rule that assumes a body only changes when its own
  row changes, which a serve-time join breaks. Applying the same watermark test
  to the *source* row makes the refresh both arrive and stop.
- A whole-session aggregate must ride the live poll's refresh allowlist.
  `background_shells` changes without any span the client watches changing, so
  pinned to the page-load row the block goes on claiming a killed shell is
  running.
- A real outcome always wins: ending a session does not rewrite a shell that
  already said how it finished.

## Acceptance

```gherkin
Scenario: the launch is marked before the harness answers
  Given a PreToolUse Bash payload with tool_input.run_in_background = true
  When the pending span is emitted
  Then its attributes carry run_in_background = true

Scenario: the completion is joined onto the launching call
  Given a tool.Bash span at T0 carrying background_task_id "bo58vwcaw"
  And a task.notification at T0+30s with that task_id, status "completed"
  When the window is merged
  Then the Bash span reads completed, with a 30000 ms shell elapsed
  And its duration_ms is unchanged

Scenario: a running shell counts up from its launch
  Given a background shell with no completion
  Then the row shows a ticking elapsed, not only the 3 ms handoff

Scenario: the card follows the completion without a reload
  Given a settled Bash span the client already holds, below its watermark
  When the completion lands afterwards as a new row
  Then the Bash span's body is re-sent and the card reads completed
  And once the client has read past that row the body is withheld again

Scenario: a shell the harness killed settles from its own output file
  Given a background shell with no completion notification
  And its output file ends in the line "[killed]"
  When the session is rescanned
  Then one task.notification is posted on the span id the hook would have used
  And the roster reads killed, so the block stops listing it
  And the launching card reads killed with the shell's true elapsed

Scenario: running shells are listed at the foot of the feed
  Given two background shells with no completion
  Then the block lists both and says "2 running"
  And a shell that completes drops out of it

Scenario: expanding a running shell streams its output
  Given a completed sibling task stated the output directory
  When a running shell's row is expanded
  Then the tail of its output file is shown, and re-read as it grows

Scenario: a shell with no locatable output says so
  Given a session that has stated no output directory
  Then the expanded shell reads "no readable output yet"

Scenario: an ended session has no running shells
  Given a launch nothing ever reported the end of
  And the session has ended
  Then the shell reads stopped, and the block is gone

Scenario: one envelope settles every task it names
  Given a notification naming two task ids and an __orphan_summary marker
  Then both tasks are settled and the marker is not treated as a task

Scenario: a notification the hook never saw is recovered
  Given a task notification present in the transcript but in no span
  And a shell that never settled
  When the live rescan runs
  Then the notification is posted and the shell settles

Scenario: the block follows the roster on the live poll
  Given the block says one shell is running
  When a notification settles it
  Then the block disappears without a reload
```

