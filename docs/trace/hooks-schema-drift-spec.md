# Implementation Spec — Hooks Schema Drift

Extend regin's existing **tool** schema-drift system to cover **all Claude Code hook
events**, not just `PostToolUse`. Same pipeline, same UI, one new orthogonal axis.

Status: proposed (2026-05-30). Author: design pass off `/deep-research` of the
official Claude Code hooks contract + the local `hook-payloads.jsonl` ground truth.

---

## 1. Motivation

The tool drift panel detects when Claude Code's `PostToolUse` payloads diverge from
the baseline JSON Schemas we vendor in `lib/trace/payload_schemas/claude/`. That
catches *tool-shaped* churn (a new `tool_response` field, a type change) but is blind
to the other ~16 hook events Claude Code emits. Those payloads evolve too — the
research surfaced dated examples (`duration_ms` added in v2.1.119, `MessageDisplay`
in v2.1.152, `notification_type` docs-vs-runtime drift in issue #11964) — and we
currently log them to `~/.claude/hook-payloads.jsonl` but never validate them.

**Goal:** every hook event gets a baseline schema; observed payloads are validated
against it; drift surfaces in the same panel with the same ratify/ignore/overlay
workflow. The highest-value signal is **`unknown_event`** — a hook event type we've
never seen a baseline for, which is exactly how Anthropic shipping a *new* event
(the `MessageDisplay`-style churn) becomes visible.

## 2. Ground truth (local `hook-payloads.jsonl`, n=6299)

17 distinct events already observed locally. This is the enumerated "all observed
events" set the first cut targets. **`PostToolUse` is excluded from hook baselines**:
the handler routes it to the tool validator (`validate()`), so its drift already
surfaces per-tool on the Tools axis — a hook-level `PostToolUse` schema would be an
inert, misleading "clean" row. That leaves **16** hook-event baselines.

| Event | n | Notable event-specific keys |
|---|---|---|
| `PreToolUse` | 2912 | tool_name, tool_input, tool_use_id, permission_mode |
| `PostToolUse` | 2843 | + tool_response, duration_ms |
| `SubagentStart` | 190 | agent_id, agent_type |
| `SubagentStop` | 184 | agent_transcript_path, last_assistant_message, background_tasks, session_crons |
| `UserPromptSubmit` | 52 | prompt |
| `PostToolUseFailure` | 32 | error, is_interrupt, duration_ms |
| `Stop` | 24 | stop_hook_active, last_assistant_message, background_tasks, session_crons |
| `CwdChanged` | 22 | old_cwd, new_cwd |
| `Notification` | 14 | message, notification_type |
| `TaskCompleted` | 10 | task_id, task_subject, task_description |
| `TaskCreated` | 5 | task_id, task_subject, task_description |
| `SessionEnd` | 3 | reason |
| `StopFailure` | 3 | error, last_assistant_message |
| `PermissionRequest` | 2 | tool_name, tool_input |
| `SessionStart` | 1 | source, model |
| `InstructionsLoaded` | 1 | file_path, memory_type, load_reason |
| `UserPromptExpansion` | 1 | expansion_type, command_name, command_args, command_source, prompt |

### 2.1 The hook envelope (empirically derived — do NOT reuse the tool envelope)

Present on **all 17** events → the hook common envelope:

```
session_id, transcript_path, cwd, hook_event_name
```

**`permission_mode` is NOT universal** (absent on CwdChanged, Notification,
SessionStart/End, SubagentStart, Task*, InstructionsLoaded). The current
`_ENVELOPE_KEYS` in `payload_validation.py` is PostToolUse-flavored
(`permission_mode`, `tool_use_id`, `agent_id`, `duration_ms`, `effort`,
`permission_decision`) — **reusing it for hook events would mass-false-positive.**
Everything beyond the four universal keys belongs *in the per-event schema*, not the
envelope.

`agent_type` is near-universal but **partial** (e.g. 2580/2843 on PostToolUse). The
gaps are real version-evolution drift — `agent_type` was added at some point. Treat
it as a normal schema property (so the partial-presence history is visible), not an
envelope key.

## 3. Design decisions (locked)

1. **Discriminator column, one shared table/pipeline/UI** (chosen). Add
   `subject_kind ∈ {'tool','hook_event'}` to `payload_schema_drift`. This is the new
   orthogonal axis (per the `sessions.origin` vs `agent_type` precedent). The
   existing `tool_name` column carries the *subject identity* — a tool name when
   `subject_kind='tool'`, an event name when `subject_kind='hook_event'`.
   - **Known tradeoff:** storing event names in a column physically named
     `tool_name` is a column-level overload. We keep the name to avoid a destructive
     rename across SQL + ORM + blueprint + frontend. A `tool_name → subject` rename
     is an *optional follow-up* (§9), not part of this cut.
2. **Input payloads only.** We validate what Claude Code *sends* hooks (the external
   contract that drifts). Hook *output* (what regin's handlers emit back) is regin's
   own contract — explicitly **out of scope** (see §9).
3. **All observed events.** Baselines bootstrapped from the JSONL (§5), cross-checked
   against the SDK `BaseHookInput` types where they exist.
4. **`unknown_event` is a first-class drift kind** — the hook analog of
   `unknown_tool`, and the single highest-value output of this feature.

## 4. Data model

### 4.1 `db/schema.sql` (fold-in required — fresh installs build from here, not Alembic)

```sql
-- add column
subject_kind  TEXT NOT NULL DEFAULT 'tool',   -- 'tool' | 'hook_event'

-- replace the unique constraint
CONSTRAINT uq_payload_schema_drift_key
    UNIQUE (agent, subject_kind, tool_name, drift_kind, field_path, claude_version)

-- add index
CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_kind
    ON payload_schema_drift(subject_kind);
```

### 4.2 Alembic migration

`migrations/versions/xxxx_hook_subject_kind.py`:
- `ADD COLUMN subject_kind TEXT NOT NULL DEFAULT 'tool'` (existing rows backfill to
  `'tool'` automatically).
- Drop + recreate the unique constraint to include `subject_kind`. SQLite can't
  `ALTER ... DROP CONSTRAINT`, so use the batch-table rebuild
  (`op.batch_alter_table`) pattern already used elsewhere in this repo.
- Add `ix_payload_schema_drift_kind`.
- This migration is **disposable after verification** — keep the schema.sql fold-in
  and the model change, delete the migration's test fixtures once confirmed.

### 4.3 ORM `lib/orm/models/payload_schema_drift.py`

- Add `subject_kind: str = Field(sa_column=Column("subject_kind", String, nullable=False, server_default=text("'tool'"), index=True))`.
- Update `UniqueConstraint` to the 6-tuple above.

## 5. Baseline schemas

New directory: `lib/trace/payload_schemas/claude/_hooks/<EventName>.schema.json`
(sibling to the per-tool files; `_hooks/` keeps the namespaces from colliding —
e.g. nothing prevents a future tool named `Stop`).

Each schema mirrors the tool-schema conventions: `additionalProperties: true`,
`x-claude-versions: []`, snake_case properties, the four envelope keys *omitted*
(handled by `_HOOK_COMMON_KEYS`), event-specific keys declared.

### 5.1 Bootstrap CLI

`cli/regin.py bootstrap-hook-schemas` (new subcommand):
- Read `provider.hook_payload_log_path()` (the JSONL).
- Group entries by `hook_event`.
- For each event, infer a schema: union of observed top-level keys → properties with
  inferred JSON Schema `type` (reuse `_inferred_type`-style logic). `required` is left
  **empty** — presence in a finite sample is not evidence a field is mandatory (e.g.
  `agent_id` is on every main-agent payload but absent on subagent ones), and a false
  `missing_required` finding isn't ratifiable, only ignorable. New fields are still
  caught as the ratifiable `unknown_field`. *(Implemented: the live `agent_id` case on
  `PostToolUseFailure` proved the 100%-presence heuristic brittle on first contact.)*
- Stamp `x-claude-versions` with `current_claude_version()`.
- Write to `_hooks/<Event>.schema.json` only if absent (don't clobber human edits);
  `--force` to regenerate.
- This is a one-shot generator — the SDK `BaseHookInput` + per-event types
  (`anthropics/claude-agent-sdk-python/.../types.py`) are a useful cross-check to
  hand-tighten the generated stubs, not a runtime dependency.

## 6. Validation (`lib/trace/payload_validation.py`)

The walker/jsonschema machinery is already subject-agnostic. Changes:

1. **`DriftFinding`** gains `subject_kind: str = 'tool'`.
2. **Path helpers** take `subject_kind`:
   `baseline_schema_path(agent, subject, subject_kind)` →
   `_BASELINE_DIR/agent/_hooks/<subject>.schema.json` when `hook_event`, else current
   behavior. Same for `overlay_schema_path` and `_load_schema`'s cache key.
3. **Envelope split.** Add
   `_HOOK_COMMON_KEYS = frozenset({'session_id','transcript_path','cwd','hook_event_name'})`.
   In `_walk_dict`, pick the envelope set by `subject_kind` (tool → existing
   `_ENVELOPE_KEYS`; hook_event → `_HOOK_COMMON_KEYS`).
4. **New entry point** (keep `validate()` for tools, back-compat):
   ```python
   def validate_event(event_name: str | None, payload: dict,
                      agent: str = _DEFAULT_AGENT) -> list[DriftFinding]:
       # guards: payload is dict, event_name present
       # schema = _load_schema(agent, event_name, subject_kind='hook_event')
       # if None -> single DriftFinding(drift_kind='unknown_event', ...)
       # else -> _jsonschema_findings + _walk_unknown_fields with subject_kind
   ```
   `_is_postool_event` stays as the guard inside `validate()`; `validate_event` has no
   such gate (it's called per-event by the handler).

## 7. Store (`lib/trace/payload_drift_store.py`)

- `_UPSERT_SQL`: add `subject_kind` to the column list, the `VALUES`, and the
  `ON CONFLICT(...)` key → `(agent, subject_kind, tool_name, drift_kind, field_path, claude_version)`.
- Bind `"subject_kind": f.subject_kind` in `record_findings`.

## 8. Handler gate (`hook_manager/handlers/trace_payload.py`)

`_record_drift` today early-returns unless `payload.event == 'PostToolUse'`. Widen it:

```python
def _record_drift(payload):
    agent = getattr(payload.resolved_provider, 'provider_id', 'claude')
    if payload.event == 'PostToolUse':
        findings = validate(payload.tool_name, payload.raw, agent=agent)
    else:
        findings = validate_event(payload.event, payload.raw, agent=agent)
    if findings:
        record_findings(findings, payload.raw)
```

Keep the wholesale `try/except` — drift tracking stays observability, never a gate.
**Cost note:** clean payloads short-circuit (`record_findings([])` opens no DB
session), `_load_schema` is `lru_cache`d, and the whole path is gated on
`diagnostics_enabled`. Widening the gate is cheap in steady state.

## 9. API (`web/blueprints/schema_drift.py`)

- All listing/summary SQL: select + group by `subject_kind`; accept a
  `?kind=tool|hook_event` filter (default: all).
- `_known_tools()` → add `_known_hook_events()` that lists `_hooks/*.schema.json`
  stems ∪ observed-but-baseline-less events. The `/schemas` endpoint emits rows for
  both kinds (carry `subject_kind` in each row).
- `api_schema_drift_schema` / `schema/diff` / `<id>/detail`: take `kind` (default
  `tool`) and thread it into the path helpers.
- **Ratify:** `_load_or_seed_overlay` and the overlay title/path must honor
  `subject_kind` (overlay lands in `_hooks/`, title "<Event> hook payload (user
  overlay)"). `unknown_event` is **not** ratifiable (parallels `unknown_tool`) —
  return 400 like the other non-`unknown_field` kinds; the fix is to bootstrap a
  baseline for that event, not to extend an overlay.
- Nav-badge summary (`/summary`) counts all pending regardless of kind — hook
  findings flow into the existing badge for free.

## 10. UI (`frontend/src/views/SchemaDriftView.vue`)

- Add a **kind axis**: a top-level toggle/segmented control `Tools | Hooks` (or two
  grouped sections). Hooks rows list by event name; reuse the existing schema / diff /
  findings tab components unchanged (they're subject-agnostic once the row carries
  `subject_kind` + `subject`).
- KPI tiles gain a hooks breakdown (or a combined count with a kind column).
- `useDriftSummary.js` needs no change (badge already aggregates pending).
- Verify in-browser per the project convention (render the actual panel; localStorage
  token bypasses the auth guard for Playwright).

## 11. Tests

- `tests/trace/test_payload_validation.py`: hook-event cases — clean payload → no
  findings; unknown top-level field → `unknown_field`; unseen event → `unknown_event`;
  envelope keys (the four) never flagged; `permission_mode` on a non-tool event *is*
  flagged as unknown_field if not declared (regression guard for the envelope split).
- `tests/trace/test_payload_drift_store.py`: UPSERT dedupes on the 6-tuple;
  `tool` and `hook_event` rows with the same `tool_name`/event string don't collide.
- Bootstrap CLI: run against a synthetic JSONL fixture, assert generated schema shape.
  (Portable — synthesize spans/payloads, `is_test=true`; never hardcode local IDs.)
- E2E: Hooks tab renders, a seeded hook drift row ratifies into a `_hooks/` overlay.

## 12. Out of scope / follow-ups

- **Hook output validation** — validating the `hookSpecificOutput` regin's own
  handlers emit (permissionDecision, additionalContext, continue, stopReason, …).
  This is regin's contract, a different feature; defer.
- **`tool_name → subject` physical rename** — cleaner column naming once this
  stabilizes; mechanical but touches SQL/ORM/blueprint/frontend.
- **Auto-bootstrap on unknown_event** — when an `unknown_event` finding appears,
  offer a one-click "generate baseline from observed payloads" in the UI.
- **SDK type vendoring** — pull `BaseHookInput` + per-event types as committed
  reference baselines for events too rare to infer reliably (e.g. SessionStart,
  InstructionsLoaded, UserPromptExpansion each n=1 locally).

## 13. Suggested sequencing

1. Model + schema.sql + migration (`subject_kind`, constraint, index).
2. Validation: `DriftFinding.subject_kind`, path helpers, `_HOOK_COMMON_KEYS`,
   `validate_event`. Unit tests.
3. Store UPSERT + handler gate widening. Smallest end-to-end slice: hook drift now
   lands in the table.
4. Bootstrap CLI + generate the 17 baselines; hand-tighten against SDK types.
5. API `kind` plumbing + ratify overlay routing.
6. UI Hooks axis + browser verify.
