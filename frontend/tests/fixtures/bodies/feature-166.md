# F166 Session Trace追踪问题排查 — tool spans render as a bare tool name

## Intent

Reported: some traces show `tool.Bash` (and other tool) spans with only the tool name and no
command. Example trace: `6926869d-9d45-4e4b-9bab-fdc60c07eeda`. Find the cause and fix it.

A tool span in the trace view must always show what was called (the Bash command, the file
path, the query). This must hold even when only one of the two writers recorded the call.

## Diagnosis (established, with evidence)

`6926869d…` is the child session of SDK run `sdk-2e4018ec277f`. Its spans come from two
writers, and the read path unions them (`lib/trace/alias.py`):

| writer | trace_id | tool rows it wrote for this session |
|---|---|---|
| child `claude` hooks (`hook_manager` PostToolUse) | `6926869d…` | **0** (only `harness.*` and `hook.stop_summary`) |
| runner SDK stream (`lib/agent_events/spans.py`) | `sdk-2e4018ec277f` | `pending-<tu>` PENDING rows **with** `command_preview`, plus resolved rows with **only** `{tool_name, tool_use_id}` |

The serve-time merge (`merge_spans` → `_drop_superseded_placeholders`) retires the
`pending-<tu>` row once the resolved row exists. The resolved SDK row carries no input, so the
view shows `tool.Bash` with nothing else.

Two causes combine. Both are needed to produce the symptom:

| # | cause | where | status |
|---|---|---|---|
| C1 | `_tool_result_span` builds the resolved row from `ToolResult`, which carries no input. Only the `ToolCall` placeholder has input-derived attrs. The merge copies the placeholder's turn linkage to the survivor (`_inherit_turn_linkage`) but not its input attrs. | `lib/agent_events/spans.py:75`, `lib/trace/merge_gates.py:12` | **this feature fixes it** |
| C2 | Every regin `hook_manager` entry was removed from `~/.claude/settings.json` on 2026-10-22 06:23:30 (local time). The removal came from `regin init --force`, run by `scripts/setup.sh` in a second, older checkout, `/Users/taowang/python-project/regin` (at `06dffb67`, 2026-10-09). That version's `_clear_hook_manager_settings` strips every checkout's entries. `regin hooks status` now reports `hook_manager  not installed`. | other checkout; `~/.claude/settings.json` mtime 06:23 | already fixed on master: removal is scoped to this checkout (`tests/hooks/test_hooks_checkout_ownership.py`), and `setup.sh` no longer runs `init --force` (`tests/cli/test_setup_script.py:141`). Restoring the wiring is an operator act, outside this build. |

Spread (agent_runs grouped by day): every SDK run from 10-05 to 10-21 has child hook tool rows.
Both runs on 10-22 (`6926869d…`, `d75c2df4…`) have none. Before C2, the child's hook row (with
full input) paired with the SDK row and hid C1. C1 is latent for any SDK child whose hooks are
not wired, for example a cwd without regin hooks. `alias.py` already treats that case as
reachable.

## Shape

```
ToolInputAttrs := the flat keys apply_pending_input_attrs(tool, input) writes
                  (Bash: command_preview, …; WebSearch/WebFetch: query/url keys)
                | {tool_input: dict}            -- fallback for tools with no builder

Runner ledger (runner_stream):  tool_use_id -> { tool_name, input_attrs: ToolInputAttrs }
                                 (today: tool_use_id -> tool_name)
```

## Rules

### R1 — producer: the resolved SDK row carries the call's input (fixes new rows)

| event | row written | input attrs on it |
|---|---|---|
| `ToolCall` | `pending-<tu>`, PENDING | `ToolInputAttrs` (unchanged) |
| `ToolResult`, ledger has `tu` | resolved `tool.<Name>` | the same `ToolInputAttrs` the ledger recorded for `tu` |
| `ToolResult`, ledger missing `tu` | resolved `tool.<Name>` (name `unknown`, as today) | none (no input known) |

The ledger entry is written and popped at the same points as today's `_tool_names` entry. Its
lifetime does not change.

### R2 — serve time: the survivor inherits input from the placeholder it retires (fixes existing rows, including the cited trace)

When a resolved `tool.*` span supersedes its `pending-<tu>` placeholder (the same pairing
`_classify_supersessions` / `_inherit_turn_linkage` already make):

| survivor has key K | placeholder has K | result |
|---|---|---|
| yes | any | survivor's K kept (never overwritten) |
| no | yes, and K ∈ ToolInputAttrs keys | K copied onto the survivor |
| no | no | nothing |

### Invariants

- `merge_spans` stays pure. Inheritance returns a copy and writes nothing to the DB.
- When the child hook row exists, the cross-source dedup result does not change: hook-side
  attrs win, and R2 never overwrites them.
- Only input-derived keys are inherited. `live`, `status_code`, and the span id stay as they are.

## Acceptance

```gherkin
Scenario: the reported trace shows its commands
  Given trace 6926869d-9d45-4e4b-9bab-fdc60c07eeda as stored today (no rows rewritten)
  When its spans are read through the session map / merge path
  Then each of its 25 tool.Bash spans has a non-empty command_preview
  And each command_preview equals the transcript tool_use input.command[:200] for that tool_use_id

Scenario: a new SDK run with an unwired child persists the command on the resolved row
  Given an SDK run whose child writes no hook tool rows
  When the stream emits ToolCall(Bash, command="echo hi") then ToolResult for the same tool_use_id
  Then the persisted resolved tool.Bash row (not the pending- row) has command_preview "echo hi"

Scenario: tools without a flat builder keep their input
  Given ToolCall(Read, {"file_path": "/x"}) then its ToolResult on the SDK stream
  Then the resolved tool.Read row carries tool_input {"file_path": "/x"}

Scenario: a hook-written row is not overwritten
  Given a resolved tool.Bash row with command_preview "A" superseding a pending- row with command_preview "B"
  When merge_spans runs
  Then the survivor's command_preview is "A"

Scenario: a result with no recorded call stays bare
  Given a ToolResult whose tool_use_id the runner ledger never saw
  Then the resolved row is written with tool_name "unknown" and no input attrs, and nothing raises
```

## Non-goals

- Fixing C2 in code. Master already scopes `init --force`. Operator remediation:
  `regin hooks repair --provider claude` (or `hooks install`) in this checkout, then update or
  retire `/Users/taowang/python-project/regin` so its old `setup.sh` cannot strip the wiring
  again.
- Backfilling or rewriting stored rows. R2 covers history at read time.
- Follow-up suggestion, not in scope: `doctor`, or a banner, could warn when SDK children are
  being traced while `hook_manager` is not installed.
