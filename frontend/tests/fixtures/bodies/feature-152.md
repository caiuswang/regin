# F152 Session列表页优化

## Intent

Many sessions have no explicit title, so the Sessions list falls back to the
first prompt. When that prompt starts with a skill invocation on its own line,
the row shows only the skill name (`titlePreview` keeps line 1 only). Rows end up
looking the same, and the operator can't tell what each session was doing.
Task-launched sessions also get an auto `feature-<id>` tag chip, which clutters
the tag column.

This feature makes a session row read like a Task row:
- The title comes from the launching task when nothing better exists.
- The feature the session belongs to is shown as a `#<id> <feature title>` line above the title, not as a tag.
- The title can wrap to more than one line.
- The filters gain a Feature picker.

## Non-goals

- Changing ingest-time title precedence (`lib/trace/trace_service/ingest.py` upsert CASE). The task title is resolved when the list is served, never written into `sessions.title`.
- Making Sessions search match the task title. (Possible follow-up.)
- Removing user-authored (`source='manual'`) custom tags or the builtin category tag.

## Shape

```ts
// Added to each row of the session list API (and the single-session payload
// SessionTraceHeader reads), next to the existing fields
type SessionRow = {
  title: string | null          // unchanged: stored sessions.title
  title_source: string | null   // unchanged
  display_title: string | null  // the resolved title (rules R1). The UI renders this one
  display_title_source: TitleSource | 'task' | null
  task: { id: number, title: string } | null          // launching task (rule T1)
  features: { id: number, title: string, is_primary: boolean }[]  // rule F1, primary first
  tags: Tag[]                   // no longer contains auto `feature-*` (rule G1)
}
type TitleSource = 'user' | 'manual' | 'user_rename' | 'claude_ai_title'
                 | 'workflow_name' | 'first_prompt'

// GET /api/sessions accepts a new query param
feature_id?: number             // narrows to sessions attributed to that feature (rule F2)
```

## Rules

### R1: display_title resolution, evaluated in order and first match wins

| # | Condition | display_title | display_title_source |
|---|---|---|---|
| 1 | `title_source` ∈ {user, manual, user_rename, claude_ai_title, workflow_name} and `title` non-empty | `title` | `title_source` |
| 2 | `task` is non-null | `task.title` | `task` |
| 3 | `title` non-empty (i.e. `first_prompt`) | `title` | `first_prompt` |
| 4 | otherwise | null (UI shows "no prompt", as today) | null |

So the AI title (`claude_ai_title`) beats the task title, the task title beats
the first prompt, and sessions with no task behave exactly as before.

### T1: which task launched a session

- The task is the one whose `agent_task_runs` row produced the session. Match `run.trace_id` collapsed through the same canonical-trace rule `lib/features/attribution.py` uses (`CANONICAL_TRACE`/`agent_run_sessions`), so the real child CLI session matches, not just the `sdk-*` placeholder.
- If several runs map to one session, use the run with the highest `agent_task_runs.id`.
- Load the tasks with one bulk query per page, not one query per row.

### F1: features shown on a row

- The source is the same merged reader the session header uses: `attribution.attributions_for_session` semantics, i.e. declared `session_features` ∪ the task lane. Titles come from `features.title`.
- Load them in bulk per page.
- The row shows the primary feature (or the first one if none is primary) as `#<id> <title>` on its own line above the title, styled like `TaskRow`'s `.task-feature-line`. If there are more features, a `+N` hint follows, and its hover tooltip lists them all.
- Rows with no features show no feature line, and the title row doesn't move.

### F2: Feature filter

- `SessionFiltersPopover` gets a **Feature** field using the existing `FeaturePicker` (same as `TaskFilterMenu`: search, then pick a chip).
- The selection is a URL query axis (`feature_id`, owned by `useSessionFeatureFilter`; not in localStorage like the other facets), so reloading keeps it. It appears in `SessionActiveFilters` as `#<id> <title>` and can be cleared there.
- The server narrows by attribution membership (same set as F1). It ANDs with every other filter, and facet counts (tag/repo/status) are computed with the filter applied.

### G1: feature tags go away

- `_auto_tag_feature` in `lib/tasks/runs.py`, and both call sites, are removed. No new `feature-*` session tags get written.
- An Alembic data revision deletes existing rows: `DELETE FROM session_tags WHERE source='auto' AND tag LIKE 'feature-%'`. There's no `db/schema.sql` change because the table shape doesn't change.
- Once those rows are gone, the tag facet no longer lists `feature-*` options.

### M1: multi-line title

- The row title renders `display_title` with newlines collapsed to single spaces. It's clamped to **2 lines** (`line-clamp: 2`) instead of first-line-only plus a 70-char cap. A skill-name first line is therefore followed by the real prompt text.
- The full title stays in the `title` attribute.
- A feature ref in the title (`[F<id> …](feature:<id>)`, which task titles and the prompts their runs open with both carry) renders as its words through `taskTitle()`, the same as `TaskRow`. The trace header shows the same text, and its source badge reads `task` when the task title won.
- `SessionCard` (mobile) uses the same feature line and the same 2-line clamp. Both components read one shared helper in `utils/sessionRowFormat.js`, so desktop and mobile never disagree.

## Invariants

- `sessions.title` / `title_source` are never written by this feature. Resolution happens only at serve time.
- The list API and the trace header return the same `display_title` for the same session, from one resolver function.
- A session with no task run and no feature attribution renders exactly as it did before, apart from the 2-line clamp.

## Acceptance

```gherkin
Scenario: task title replaces first prompt
  Given session S has title_source='first_prompt' title="/feature-pipeline\nfix the thing"
  And S was produced by a run of task 451 titled "Spec F152"
  When GET /api/sessions lists S
  Then S.display_title = "Spec F152" and S.display_title_source = "task"

Scenario: AI title beats task title
  Given session S has title_source='claude_ai_title' title="Refine session list"
  And S was produced by a run of task 451
  Then S.display_title = "Refine session list"

Scenario: no task keeps the old rule
  Given session S has no agent_task_runs row and title_source='first_prompt'
  Then S.display_title = S.title and S.task is null

Scenario: run placeholder collapses to the child session
  Given run R has trace_id "sdk-x" and agent_run_sessions maps "sdk-x" to CLI session C
  Then C.task is R's task and the list does not show "sdk-x"

Scenario: feature line instead of feature tag
  Given session S is attributed to feature 152 "Session列表页优化"
  When the Sessions list renders S
  Then S's row shows "#152 Session列表页优化" above the title
  And S's tag chips contain no "feature-152"

Scenario: no new feature tags
  Given a task with feature_id=152 finishes a run
  Then session_tags has no row with tag LIKE 'feature-%' for that session

Scenario: migration removes existing auto feature tags
  Given session_tags holds (T, 'feature-7', 'auto') and (T, 'release', 'manual')
  When `regin migrate` runs
  Then only (T, 'release', 'manual') remains

Scenario: filter by feature
  Given sessions A (attributed to 152) and B (not attributed)
  When GET /api/sessions?feature_id=152
  Then A is listed and B is not
  And navigating to /trace/sessions?feature_id=152 shows the Feature chip "#152 …" in the active filters

Scenario: multi-line title
  Given display_title "/feature-pipeline\nfix the session list title logic"
  When the row renders at desktop width
  Then the title text includes "fix the session list" and is clamped to at most 2 lines
```

