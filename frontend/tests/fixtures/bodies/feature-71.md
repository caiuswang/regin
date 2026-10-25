## Reported

Sometimes the agent edits a file with `sed` or another shell tool. The Claude
Code terminal shows the bash call *and* the resulting file diff; regin's
session conversation view shows only the command executing. Make it work like
the terminal.

## Root cause

The data already arrives and regin drops it. Claude Code ≥ 2.1.270 stamps
`tool_response.bashEditDiff` on every file-mutating Bash call — regin's own
drift detector has logged it as an unknown field 193 times since 2026-09-13,
still `pending`. Nothing reads it. `hunks[].lines` are already unified-diff
body lines, the exact form `DiffBlock.vue` parses, so no diff has to be
computed anywhere.

Worst case today: a `sed -i` with no stdout and a command under the 200-char
preview cap stores no `command` attribute either, so the Bash card's attribute
guard fails and the call renders as a bare one-line row with no body at all
(3 of the 300 newest sampled `sed -i` spans are in exactly this state).

## Spec

Shape: the hook flattens `bash_edit_diff` into `attributes.bash_edits`
(`[{file_path, edit_op, diff, added_lines, removed_lines}]`) plus
`bash_edit_files` / `bash_edit_more` / `bash_edit_unavailable`, capped at
128 KB per span. The Bash card grows a fourth section that renders one
`DiffBlock` per file; `file_path` deliberately stays unset on `tool.Bash` so
no downstream edit-counting consumer changes.

**Non-goals**: computing diffs ourselves, backfilling stored spans, widening
full-text search / `file_edits` counters / repo attribution, other providers.

## Acceptance

- [ ] A Bash payload carrying one `bashEditDiff` file yields one `bash_edits`
      entry with a `@@`-led diff and matching `+`/`−` counts; `created`/`deleted`
      map to `edit_op` `create`/`delete`.
- [ ] A Bash payload with no `bashEditDiff` emits a byte-identical span to today.
- [ ] An oversized diff is cut on a line boundary with `diff_truncated_bytes`
      accounting for every dropped byte.
- [ ] A malformed file entry is dropped whole and counted into `bash_edit_more`,
      keeping `len(bash_edits) + bash_edit_more == len(bash_edit_files)`.
- [ ] `unavailable: true` renders the changed paths and no DiffBlock.
- [ ] A silent `sed -i` span (no stdout/stderr/command) renders as a Shell card
      with a caret, not a bare inline row.
- [ ] Expanding shows a DiffBlock per file under an `Update(<path>) +A −B` header;
      `bash_edit_more` paths are listed below it.
- [ ] A Bash span carrying `bash_edits` survives Focus at full tier and folds
      under `edits`.
- [ ] `tool_response.bashEditDiff` stops producing drift findings.
- [ ] `conversation-event-count.spec.js` still passes (no new rows).

---

## Intent

When the agent mutates a file through the shell — `sed -i`, a heredoc'd
Python script, `git apply`, a codegen run — the Claude Code terminal prints the
command **and** a per-file diff. regin's conversation view prints the command
and its stdout only, so the highest-signal thing the call did, what changed on
disk, is invisible. A silent `sed -i` is worse than invisible: with no stdout
and a command short enough to fit the preview cap, the span carries no
`command` attribute either, the Bash card's attribute guard fails, and the call
degrades to a bare inline row with no body at all.

The data is already arriving. Claude Code ≥ 2.1.270 stamps
`tool_response.bashEditDiff` on every file-mutating Bash call, and regin's own
drift detector has been logging it as an unknown field since 2026-09-13
(`payload_schema_drift`, 193 occurrences, status `pending`). Nothing reads it.
This spec wires that field onto the `tool.Bash` span and renders it in the Bash
card.

### Non-goals

- **Computing a diff ourselves.** No re-reading files from disk, no `git diff`,
  no `difflib` over before/after snapshots. If Claude Code did not supply a
  diff, the card says so. A diff regin reconstructs after the fact is a diff of
  a later state of the file, which is a different claim than "this call changed
  these lines".
- **Backfilling stored spans.** The transcript carries the same field on its
  `toolUseResult` rows, so `lib/trace/repair.py` *could* recover it — but
  `_synthetic_tool_response` deliberately reads only the rendered `tool_result`
  block today, and widening it is a separate change with its own blast radius.
  Follow-up, not this spec.
- **Every other consumer of "an edit happened".** Full-text search
  (`lib/fulltext/span_docs.py`), the `file_edits` session counter and
  `_REPO_EDIT_NAMES` repo attribution (`lib/trace/trace_service/ingest.py`),
  grader coverage (`lib/grader/`), the Terminal tab, and present mode are all
  keyed on the `tool.Edit`/`Write`/`MultiEdit`/`apply_patch` **name set** and
  stay exactly as they are. Widening them changes what past sessions are
  measured to have done; that is a ledger decision, not a rendering fix.
- **Non-Claude providers.** Kimi and Codex emit no equivalent field. Their
  Bash spans are unaffected.

## Model

### Source — what the hook receives

```ts
// PostToolUse payload: tool_response.bash_edit_diff
//
// `hook_manager.core._normalize_payload` snake-aliases keys ONE level into
// `tool_response`, so the container is reachable as `bash_edit_diff`; every
// key BELOW it stays camelCase (the same asymmetry documented in
// `lib/trace/payload_validation.py`).
type BashEditDiff = {
  files: Array<{
    filePath: string
    hunks: Array<{
      oldStart: number; oldLines: number
      newStart: number; newLines: number
      lines: string[]          // already unified-diff body lines: "+…" "-…" " …"
    }>
    created?: true              // the command created the file
    deleted?: true              // the command removed it
  }>                            // ≤ 5 — Claude Code's own cap
  changedFiles?: string[]       // EVERY changed path, uncapped
  moreFiles: number             // changed files shipped with no hunks
  unavailable?: true            // diffing failed (binary, sqlite, …)
  shared?: true                 // Claude Code bookkeeping; see rule C7
}
```

Measured over 562 real occurrences in `~/.claude/projects/-Users-taowang-regin`:
`files` never exceeds 5; serialized size p50 3 KB, p90 11 KB, max 67 KB; the
per-file key set is exactly `{filePath, hunks}` ± `created` ± `deleted`; the
hunk key set never varies.

The decisive property: **`hunks[].lines` are already unified-diff body lines**,
the same form `post_tool_trace._compute_unified_diff` emits for Edit and Write,
which is the form `DiffBlock.vue` parses. Nothing in this feature re-diffs
anything; it reformats hunk bounds into `@@` headers and concatenates.

### Target — what lands on the span

```ts
// Added to `attributes` of a `tool.Bash` span. Every key is omitted when empty.
type BashEditAttrs = {
  bash_edits?: BashFileEdit[]
  bash_edit_files?: string[]     // changedFiles, verbatim
  bash_edit_more?: number
  bash_edit_unavailable?: true
  added_lines?: number           // Σ over bash_edits
  removed_lines?: number         // Σ over bash_edits
}

type BashFileEdit = {
  file_path: string
  edit_op: 'create' | 'update' | 'delete'
  diff: string                   // "@@ …" headers + body, no ---/+++ header
  added_lines: number
  removed_lines: number
  diff_truncated_bytes?: number
}
```

`added_lines` / `removed_lines` / `edit_op` / `diff` are the names the Edit path
already uses (`post_tool_trace._attach_edit_diff`), reused here at both levels
so one vocabulary covers both kinds of edit.

### Why a list, and not the Edit shape

An Edit span is one file; a Bash span is zero-to-many. Two shapes were
considered and one rejected:

| Option | Verdict |
|---|---|
| Flatten to top-level `file_path` + `diff` + `edit_op` when exactly one file, list otherwise | **Rejected.** Two shapes for one fact, and it makes `tool.Bash` indistinguishable from an edit span to every consumer that keys on `attributes.file_path` — silently changing repo attribution, session file lists and the file-history join, which is the opposite of the scope this defect asks for. |
| One `bash_edits` list, always | **Chosen.** One shape; every existing `file_path` consumer keeps reading nothing from a Bash span. |

## Rules

### C — Capture (`post_tool_trace._build_bash_attrs`)

| # | `tool_response.bash_edit_diff` | Effect |
|---|---|---|
| C1 | absent, or not a dict | nothing written |
| C2 | `files` empty **and** `changedFiles` empty/absent | nothing written |
| C3 | `files` non-empty | `bash_edits`, plus `added_lines` / `removed_lines` totals (omitted when 0) |
| C4 | `changedFiles` a non-empty list of strings | `bash_edit_files` = that list, verbatim |
| C5 | `moreFiles` > 0, or entries dropped by F7 | `bash_edit_more` = `moreFiles` + dropped count |
| C6 | `unavailable: true` | `bash_edit_unavailable: true` |
| C7 | `shared` | ignored — it is Claude Code's own display bookkeeping, not a fact about what this call changed |
| C8 | any exception while parsing | swallowed; the span is emitted with today's attrs. A trace row must never be lost to a rendering nicety. |

### F — Per file entry

| # | Entry | `edit_op` | `diff` |
|---|---|---|---|
| F1 | `created: true` | `create` | hunks joined |
| F2 | `deleted: true` | `delete` | hunks joined |
| F3 | neither | `update` | hunks joined |
| F4 | `hunks` absent or empty | per F1–F3 | `''` — the path and the op are still the fact |
| F5 | joined text longer than the remaining budget | per F1–F3 | newline-aligned cut (`_truncate_diff`), `diff_truncated_bytes` set |
| F6 | budget already exhausted | per F1–F3 | `''`, `diff_truncated_bytes` = full length |
| F7 | not a dict, or `filePath` missing/not a string | — | entry dropped **whole**, never partial (the rule `_parse_hunk` already holds), and counted into `bash_edit_more` |

Hunk → text: `@@ -{oldStart},{oldLines} +{newStart},{newLines} @@` followed by
`lines`, joined with `\n`; hunks joined with `\n`. Bounds are read through the
existing `_hunk_int` helper, which accepts both spellings — required, because
the normalizer does not reach nested keys.

Budget: `_BASH_EDIT_DIFF_MAX = 128 * 1024` per span, consumed in file order.
A ceiling for the pathological case, not a routine cut (p90 is 11 KB). It
exists because the conversation view reads the **shallow** map
(`sessions.py::_shallow_map_response`), which ships span attributes inline —
unlike the stripped structural `/map`, whose bodies arrive lazily through
`/spans/<id>/content`.

### R — Render (conversation view)

| # | Span state | Collapsed `Shell` row | Expanded body |
|---|---|---|---|
| R1 | no `bash_edit*` attrs | unchanged | unchanged (command / stdout / stderr) |
| R2 | `bash_edits` non-empty | gains, before the duration: `N file(s)`, `+A`, `−B` in DiffCard's emerald/red | a fourth section under stderr: one sub-header `Create\|Update\|Delete(path) +A −B` + `DiffBlock` per entry |
| R3 | an entry with `diff: ''` and `diff_truncated_bytes` | as R2 | sub-header only, with `diff too large (N)` where the DiffBlock would be |
| R4 | an entry with `diff: ''` and no truncation | as R2 | sub-header only, with `no diff supplied` |
| R5 | `bash_edit_more` > 0 | count in R2 is `len(bash_edits) + bash_edit_more` | trailing line: the extra paths from `bash_edit_files`, or `+N more files` when `bash_edit_files` is absent |
| R6 | `bash_edit_unavailable` | `N file(s)` with no `+/−` | trailing line: `diff unavailable` + the `bash_edit_files` paths |
| R7 | `bash_edits` empty but `bash_edit_files` present | `N file(s)` | the paths alone, no diff section |

Load-bearing edits beyond the card body:

| # | Where | Change |
|---|---|---|
| R8 | `ConversationSpanCard.vue` Bash branch guard | add `\|\| attributes.bash_edits \|\| attributes.bash_edit_files` — without it a silent `sed -i` still falls through to the bare inline row, which is the sharpest form of this defect |
| R9 | `BashCard.hasBody` | same widening, so the disclosure caret appears |
| R10 | `dispatch.js::tierOf` | a Bash span carrying `bash_edits` is `TIER_FULL`. The tier's stated charter is "anything that changed the world"; a shell call that rewrote a file did |
| R11 | `dispatch.js::foldCategory` | the same span folds under `edits`, not `tool calls` |
| R12 | new `cards/BashEditDiffs.vue` | the R2–R7 body lives in its own child. BashCard already carries three sections; a fourth inline would breach the `vue-complexity` template metrics the BundleEngine gates on |

`rendersInConversation` needs **no** change: it already returns true for every
`tool.` prefix, and no new rows are created.

### S — Schema and drift

| # | Rule |
|---|---|
| S1 | `lib/trace/payload_schemas/claude/Bash.schema.json` gains `tool_response.bash_edit_diff` as a *described opaque object* — exactly how `git_operation` is carried, so Claude Code's varying internals never register as drift — and `2.1.270` joins `x-claude-versions`. |
| S2 | After S1, no new `payload_schema_drift` row is written for this field. The two existing `pending` rows are the operator's to resolve in `/drift`; this change does not touch stored rows. |

## Invariants

- **I1** — For every `tool.Bash` span, `bash_edits` is present ⟺ Claude Code
  reported at least one diffable mutated file. It is never synthesised, and
  never inferred from the text of the command.
- **I2** — For every entry `e` in `bash_edits`, `e.diff` contains no `---` or
  `+++` header line. `DiffBlock` synthesises its own header from the path; this
  is the same contract Edit spans already hold.
- **I3** — For every span where `bash_edit_files` is present,
  `len(bash_edits) + (bash_edit_more or 0) == len(bash_edit_files)`. Rule F7
  bumps `bash_edit_more` precisely so that dropping a malformed entry cannot
  break this.
- **I4** — No attribute outside `bash_edit*` / `added_lines` / `removed_lines`
  changes on any Bash span. In particular `file_path` stays unset on
  `tool.Bash`, so every consumer keyed on "the span has a file_path" — repo
  attribution, session file lists, the file-history join — sees exactly what it
  sees today.
- **I5** — A Bash call that mutated no file produces a byte-identical span to
  today's, at every layer.
- **I6** — The number of rows the conversation feed renders is unchanged, so
  the header count and the DOM stay in agreement (`conversation-event-count.spec.js`).
- **I7** — For every span, `len(json(attributes.bash_edits)) ≤ _BASH_EDIT_DIFF_MAX`
  plus per-file header overhead. The shallow map's inline payload is bounded.

## Acceptance

Each item is a test that runs.

### Capture — `hook_manager/tests/test_post_tool_trace_contract.py`

```gherkin
Scenario: a shell edit becomes a diff on the span
  Given a PostToolUse Bash payload whose tool_response.bashEditDiff carries one
    file with one hunk
  When the post-tool trace handler emits the span
  Then attributes.bash_edits is a one-entry list
  And   its file_path, edit_op="update", added_lines and removed_lines match the hunk
  And   its diff starts with "@@ -" and contains no "---" or "+++" line
  And   attributes.added_lines and removed_lines equal that entry's totals

Scenario: created and deleted files are labelled
  Given a bashEditDiff with one created:true file and one deleted:true file
  Then their edit_op values are "create" and "delete"

Scenario: the unchanged path is untouched
  Given a PostToolUse Bash payload with no bashEditDiff
  Then the emitted attributes are byte-identical to the pinned contract dict   # I5

Scenario: a truncated diff is cut on a line boundary
  Given a bashEditDiff whose joined hunks exceed _BASH_EDIT_DIFF_MAX
  Then the stored diff ends on a newline boundary
  And   diff_truncated_bytes accounts for every dropped byte                   # I7

Scenario: a malformed file entry is dropped whole and counted
  Given a bashEditDiff with one valid file and one entry missing filePath
  Then bash_edits has one entry
  And   bash_edit_more is one greater than moreFiles                           # I3, F7

Scenario: an undiffable command still names its files
  Given a bashEditDiff with unavailable:true, no files, and two changedFiles
  Then bash_edit_unavailable is true
  And   bash_edit_files holds both paths
  And   bash_edits is absent                                                   # R6
```

### Schema — `hook_manager/tests/test_payload_validation.py`

```gherkin
Scenario: bashEditDiff is no longer drift
  Given the PostToolUse-Bash fixture extended with a real bashEditDiff object
  When the payload is validated against the Claude Bash schema
  Then no DriftFinding is produced for tool_response.bashEditDiff
    or tool_response.bash_edit_diff                                            # S1
```
Mirrors the existing `test_bash_git_operation_not_flagged_as_drift`.

### Render — `frontend/tests/bash-edit-diff.spec.js`

```gherkin
Scenario: a silent sed edit renders as a card, not a bare row
  Given a tool.Bash span with no stdout, no stderr, no command attribute,
    and one bash_edits entry
  Then the feed shows a Shell card with a disclosure caret               # R8, R9
  And   the collapsed row reads "1 file  +3  −1"                              # R2

Scenario: expanding shows the diff
  When the Shell row is clicked
  Then a DiffBlock renders the entry's hunk under an "Update(<path>)" header
  And   its added and removed lines are styled as additions and deletions      # R2

Scenario: more files than diffs
  Given a span with 5 bash_edits, bash_edit_more=4 and 9 bash_edit_files
  Then the collapsed row reads "9 files"
  And   expanded, the 4 undiffed paths are listed below the diffs             # R5

Scenario: the diff was unavailable
  Given a span with bash_edit_unavailable and 3 bash_edit_files
  Then the expanded card shows "diff unavailable" and all three paths
  And   no DiffBlock is rendered                                              # R6

Scenario: a file-editing shell call survives Focus
  Given Focus mode is on
  Then a tool.Bash span carrying bash_edits renders at full tier        # R10
  And   a tool.Bash span without it condenses as before
```

### Regression

```gherkin
Scenario: the feed still counts what it renders
  Then conversation-event-count.spec.js passes unchanged                      # I6
```

## Work

| Layer | File | Change |
|---|---|---|
| capture | `hook_manager/handlers/post_tool_trace.py` | `_attach_bash_edit_diff` + hunk→text helper, called from `_build_bash_attrs`; new `_BASH_EDIT_DIFF_MAX` |
| schema | `lib/trace/payload_schemas/claude/Bash.schema.json` | S1 |
| render | `frontend/src/components/conversation/cards/BashEditDiffs.vue` | new (R2–R7) |
| render | `frontend/src/components/conversation/cards/BashCard.vue` | summary chips on the collapsed row; mount the child; widen `hasBody` |
| render | `frontend/src/components/conversation/ConversationSpanCard.vue` | R8 |
| render | `frontend/src/components/conversation/dispatch.js` | R10, R11 |
| tests | `hook_manager/tests/…`, `frontend/tests/bash-edit-diff.spec.js` | the Acceptance section |

### Follow-ups (deliberately out of scope)

1. Backfill from `toolUseResult.bashEditDiff` in `lib/trace/repair.py`, so
   sessions traced before this lands, and hook-gapped sessions, gain diffs.
2. Index `bash_edits[].diff` in `lib/fulltext/span_docs.py::span_body` so a
   shell-made change is searchable the way an Edit is.
3. Decide whether a file-editing Bash call should count toward `file_edits`
   and `session_repos` — a ledger question, since it re-measures the past.
4. Present mode (`utils/presentModel.js`) and the Terminal tab render no Bash
   diff; the Terminal tab renders no Edit diff either, so it is consistent
   rather than broken.
