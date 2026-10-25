# Empty sessions: `claude` children that never held a conversation

22.6% of regin's session rows (743 of 3280) were `claude` processes that never
had a conversation. They were indistinguishable from real sessions in the list.

## Cause — regin was spawning most of them itself

`GET /api/sessions/<id>/bridge-commands` powers the `/live` composer's
`/`-autocomplete. Its authoritative leg is a Claude Agent SDK handshake
(`lib/agent_bridge/commands.py::_server_info_commands`): open a
`ClaudeSDKClient`, read `get_server_info()`, close. That opens a real `claude`
child, which runs regin's own hooks and then exits **without ever taking a
prompt**:

| # | span | note |
|---|---|---|
| 1-2 | `instructions.loaded` ×2 | CLAUDE.md + CLAUDE.local.md |
| 3 | `session.start` | `source=startup` |
| 4 | `environment.git_status` | |
| 5 | `session.end` | `reason=other`, ~2.3s after span 1 |

Reproduced directly: clearing `_sdk_cache` and calling `_fetch_sdk_commands()`
minted `d0ba6cb0…` — those five spans, 2.30s, `prompts=0`, `tool_calls=0`,
`cwd=/Users/taowang/regin`. 371 of the 743 carry that exact signature and 425
carry that cwd. The handshake is TTL-cached per root (300s), so the rate is one
row per cold autocomplete, which matches the observed ~20/day.

Any promptless `claude` produces the same trail — `claude < /dev/null` aborts
with *"Input must be provided either through stdin or as a prompt argument when
using --print"* and leaves the identical five spans in 2.34s — so the shape is
generic. regin merely happened to be the biggest caller.

Two smaller sources, **not** regin's to prevent: read-only CLI subcommands
(`claude mcp list` fires *only* `SessionEnd` under a fresh id → a 1-span row,
22 in the store; `claude doctor` and `claude agents --json` fire nothing), and
promptless launches by anything else on the machine.

The Python test suite is **not** a source: running it start to finish adds zero
rows to `db/regin.db`, and no empty row carries `is_test=1`.

## Design

Two layers, because the two populations are different.

### 1. Stop minting them — `PROBE_ENV` (the regin-owned source)

`hook_manager.core.PROBE_ENV` (`REGIN_INTERNAL_PROBE`) is set by
`_server_info_commands` on the child it spawns, via `ClaudeAgentOptions.env`
(which merges over the inherited environment). `hook_manager.runner.main`
drains stdin — exiting without reading it hands the CLI a broken pipe — and
returns 0 before dispatching anything.

Keyed on **the spawn**, not on what the session later turns out to contain, so
the row is never created rather than filtered back out afterwards.

Narrowing `setting_sources` would also silence the hooks (they live in
user-scope settings) but drops that scope from the answer — 136 → 115 commands
for this repo. The catalog is the whole point of the handshake, so the marker
is the lever, not the settings. Verified: 136 commands still returned, 0 rows
created.

### 2. Classify what regin does not own — the `empty` axis

743 rows already exist, and regin cannot refuse a row for a launch it did not
spawn: at `SessionStart` an aborted launch and a live session are the same
event. So `/api/sessions` carries a read-time axis, orthogonal to `origin`
(`origin` says who launched a session; `empty` says whether it did anything),
mirroring the existing `workflow=hide|show|only` shape.

```
work(session) := EXISTS span IN session : span.name = 'prompt'
                                       OR span.name LIKE 'tool.%'
```

| `empty=` | rows returned | `empty_hidden_count` |
|---|---|---|
| `show` (server default) | all | `null` |
| `hide` | `work(s)` only | count of `¬work(s)` under the same other filters |
| `only` | `¬work(s)` only | `null` |

**Span truth, not counters.** `sessions.prompts` / `sessions.tool_calls` are
bumped only by the ingest batch that inserted the span, so a prompt that
reached the store by any other path leaves the counter at 0 — 102 rows read
`prompts = 0` while holding a real `prompt` span. Keying the axis on the
counters would hide every one of them.

**Correlated `EXISTS`, and a range instead of `LIKE`.** SQLite's default LIKE
is case-insensitive so `name LIKE 'tool.%'` cannot prefix-scan, and an
`IN (SELECT …)` list is materialized by scanning every span once per statement
— page, total, active, repo facet and hidden count each paid it: 39ms → 959ms
on a 665k-span store. `EXISTS` with `name >= 'tool.' AND name < 'tool/'`
(`'/'` is `'.'`+1, so the range matches exactly what the LIKE did) searches by
trace_id and stops at the first hit: 93ms, flat to page 10. It is also
NULL-safe, which `NOT IN` over a nullable column is not.

### Invariants

- `count(empty=hide) + count(empty=only) == count(empty=show)` for any fixed
  other-filter set.
- Each hidden-count equals exactly the rows **its own** toggle reveals, with
  every other axis left in force — so a row hidden by both the `workflow` and
  `empty` axes is counted by neither.
- `empty=hide` is suppressed under an explicit `trace_id=` prefix: that is a
  deep link and must resolve its own row.
- Server default stays `show`, so no existing caller's population moves.

### Frontend

SessionsView sends `empty=hide` by default (persisted in
`regin_sessions_empty`), and renders a chip — `◌ N empty launches hidden →` —
guarded by `hiddenEmptyCount || emptyShown`, on the same toggle shape as the
system-runs chip. `resetFilters` restores hiding.

## Non-goals

- Deleting the historical rows. They are evidence, and the `session.end`-only
  shape is how `claude mcp list` is identifiable at all.
- Backfilling `sessions.prompts` / `span_count`. The counter drift is real and
  live (mostly `sdk-` aliased groups, where `_refresh_group_span_count`
  re-points `span_count` at the rendered count by design) but it is a separate
  defect; this axis is built not to depend on it.

## Follow-ups, deliberately not built

- A `SessionEnd` that is the **first and only** span regin has ever seen for a
  trace is, by event identity, not a session — it could be refused at ingest,
  which would stop the `claude mcp list` shape at source.
- The `empty` axis costs ~+58ms and scales with **session** count, not span
  count (one index probe per `sessions` row). At ~30k sessions that is ~580ms
  and it would need a flag materialised at ingest. Untested ceiling, not a
  defect at 3.3k.
- `PROBE_ENV` currently has exactly one caller. Any future regin-spawned
  `claude` that is an interrogation rather than a conversation should set it
  too.
