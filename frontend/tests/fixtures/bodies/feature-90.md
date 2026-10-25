# Enrich the pipeline by reference to open-source prior art

Notation: `docs/spec-notation.md`.

## Intent

The pipeline works. It is not yet a product. This feature is the survey that
says which of the two sentences to act on, and the menu the operator picks
from — it is deliberately a **decision feature**, not a build feature. Each
option below is independently shippable; none is started until selected.

The original question was "reference some open-source projects so our pipeline
feels like a real product". The survey answered it in an unexpected direction,
so the intent is restated: **regin's model is ahead of the field; its surfaces
and its closure loops are behind.** Copy nothing structural. Copy ergonomics.

## What the survey found

Verified against source on 2026-09-19, not against blog summaries.

| Question | The field's answer | regin today |
|---|---|---|
| Link a defect to the change that caused it | **Nobody does it.** GitLab's change-failure-rate is `SUM(incidents_count) / GREATEST(SUM(deployment_frequency), 1)` — two independent daily counts, no causal edge. Pelorus joins on an app label. Four Keys, Hygieia, metrik: archived | `defects.blamed_event_id` → the exact `feature_events` row. **Ahead** |
| Count "how many times did we think we were done and weren't" | **No OSS tool has this metric.** Middleware's `rework_cycles` is the closest and is PR-scoped. DevLake closed the request as not-planned | `1 + count(restarted)`. **Ahead** |
| Derive a defect→durable-check | **Nobody.** Every incident tool stops at "file a ticket" | `defects.missing_check`, 45/48 closed defects name a real test. **Ahead** |
| Report "which acceptance items have no test" | Serenity (tree from directory structure), OFT, LOBSTER, reqflow all do it | **Behind — no storage for acceptance items at all** |
| Turn a defect into scheduled work | Sentry, Jira, Redmine, Plane all do it | **Behind in practice** — the CLI exists, 0/60 defects use it |
| Spec revision history as evidence | Doorstop fingerprints, OFT revisions, every note product | **Absent** — `features.body` is one mutable column |

So the three headline numbers are sound and novel. The gap is that **nothing
closes the loop after `missing_check` is written**, and **the bar a feature is
judged against has no machine-readable home**.

## What we measured

Live DB, 2026-09-19.

| Fact | Value |
|---|---|
| features | 90 · 26 shipped · 5 with no meaningful spec body |
| session→feature attribution coverage | **143 of 3306 sessions (4.3%)**, $1,188 of $24,957 (4.8%) |
| features with zero attributed session | 68 of 90 — cost and rounds are unknowable for them |
| defects | 60 · 19 unattributed · 12 open · 41 carry `blamed_event_id` |
| `defects.fix_task_id` populated | **0 / 60** |
| tasks with `source='defect'` | **0** — the loop has never once run end-to-end |
| closed defects whose `missing_check` names a test | 45 / 48 (two say literally `"fixed"`) |
| acceptance-item rows | **0 — no table exists** |
| spec revisions stored | **0 — no table exists** |
| repo rework (`scripts/rework_probe.py`, 120d) | 569 commits, **34.3% are fixes**; 52.5% of replaced lines were <1 day old |
| pipeline's own features carrying defects | #76 (6), #73 (5), #27 (4), #65 (3), #30 (2) — 20 total |
| UI component count per tab | Tasks 25 · Features 12 · **Defects 4** |

The last row is the "three unrelated tables" complaint, quantified: Tasks is a
mature product, Defects is a stub wearing the same header.

## Non-goals

- **Do not replace the source-textarea spec editor with a block/WYSIWYG
  editor.** `FeatureSpec.vue` states the reason: the column holds prose agents
  both read and write, so the editor hands back the exact bytes it was given.
  Notion's block model is the wrong shape for a column with two machine authors.
- **Do not re-do `/pipeline` list-and-filter consistency.** Feature #73 owns it
  and is mid-build.
- **Do not adopt DORA's four keys.** regin has no deployments. The field's
  metrics are weaker than the ones already here.
- **No clock-based anything.** Sentry's `_handle_regression` carries a 5-second
  `active_at` buffer and it is the documented cause of its false regressions.
  It is the one heuristic in an otherwise identifier-based design; do not copy it.
- **No new spec files in `docs/`.**

## What was selected

The operator picked from the menu below on 2026-09-20. This section is the
decision; the menu is kept only to record what was weighed and declined.

| # | Option | Verdict |
|---|---|---|
| **B** | Close the defect→fix loop in the browser | **selected** — ships first |
| **D** | Defects tab reaches parity with Tasks | **selected** — absorbs B's surface, sequenced after #73 |
| **G** | Retire the doc sprawl | **selected** — rides along with B |
| A | Acceptance items become rows | declined this round |
| C | Spec revisions | declined this round |
| E | Typed relations (`caused_by`, `duplicate_of`) | declined this round |
| F | Raise attribution coverage above 4.3% | declined this round |
| H | Split the done-claim (`shipped` → `verified`) | declined this round |
| **P** | Projects tab + project panel, after Linear's project page | **selected** 2026-09-23 and built |

**B is a strict subset of D.** B is the one write path that makes
`missing_check` produce work; D is the rest of the tab. They ship as two
features rather than one so the loop closes without waiting on the surface.

**D collides with feature #73**, which is mid-build and owns `/pipeline`
list-and-filter consistency across all three tabs. D was promoted anyway, so
the sequencing constraint is explicit: **D does not start until #73 ships or is
abandoned.** If both are open, the later one rebases onto the earlier — they
edit the same views, and two features editing `DefectsView.vue` concurrently is
how one of them silently loses its filter model.

Declining A leaves the acceptance-item gap open: "which item has no test"
stays unanswerable, and `regin feature ship` still reports no bar. That is the
one place the surveyed field is ahead of regin, and it is now a known,
deliberate gap rather than an oversight.

## The options that were weighed

Each row is independently shippable. `Ahead/Behind` is versus the surveyed field.

| # | Option | Fixes | Size | Prior art |
|---|---|---|---|---|
| **A** | **Acceptance items become rows** | the bar has no machine-readable home; "which item has no test" is unanswerable | M | OFT `Needs:`, LOBSTER `requires: X or Y`, pytest-requirements (35 LOC), Serenity pending-tree |
| **B** | **Close the defect→fix→verify loop in the browser** | 0/60 `fix_task_id`, 0 tasks from defects | S | Sentry suspect-commit→assignee routing |
| **C** | **Spec revisions** | "revision count is free evidence" is claimed by the doc and stored nowhere | S | reuse in-repo `proposal_revisions` + `RevisionCompareView.vue` |
| **D** | **Defects tab reaches parity with Tasks** | 4 components vs 25; no web `merge`, no `fix-item`, no detail affordances | M | Linear Triage inbox for the 19 unattributed |
| **E** | **Typed relations (`caused_by`, `duplicate_of`, `regression_of`)** | causation is currently one nullable FK; duplicates are merged destructively | M | Redmine `TYPES` (one canonical row, inverse derived), BMO `regressed_by` kept off the `blocks` axis |
| **F** | **Raise attribution coverage above 4.3%** | 68 of 90 features have no attributed session | S | Opik `scored_at` + `sampling_per_rule` — exactly-once by identifier, no timer |
| **G** | **Retire the doc sprawl** | three overlapping descriptions; `docs/feature-pipeline.md` says to delete itself, and has 8+ contradictions with the code | S | — |
| **H** | **Split the done-claim: `shipped` → `verified`** | a ship-claim and a ship-that-held are one state | M | Sentry `GroupResolution.Status.pending` |

## Option B, in detail — close the loop

The code is already written. `regin defects fix-item <id> --create` captures a
task with `source='defect'`, the symptom verbatim as the title, and a
back-pointer body (`cli/commands/defects.py:394-412`). `DefectRow.vue:52-56`
already renders the resulting link, and `DefectsView.vue` can already scope the
list to one fix item.

What is missing is a **write control and an HTTP route**. There is no
`POST /api/defects/<id>/fix-item`, so nothing in the browser can call it; the
read surface has been shipped for data that nothing writes.

Note: feature **#63** specced exactly this and sits at `specced` while both its
stated gaps have since closed by other routes (`POST /api/defects` exists;
`parent_id` was dropped in migration `0028`; `lib/tasks/integrations/regin.py`
no longer exists). #63's spec is stale — fold it into this option or rewrite it.

```gherkin
Scenario: closing a defect offers to schedule the check it named
  Given a defect whose missing_check names a test that does not exist
  When I close it from the browser
  Then I am offered a fix task carrying that check as its body
  And accepting writes defects.fix_task_id and a task with source='defect'

Scenario: the ledger shows an unredeemed check
  Given a closed defect whose missing_check names a test
  And no collected test matches that name
  When I read the feature's ledger
  Then the defect reads "check not yet written"
```

## Option D, in detail — the Defects tab becomes a product

Sequenced after #73. The asymmetry is the whole defect: Tasks ships 25
components, Features 12, **Defects 4** (`DefectActions`, `DefectRow`,
`DefectsScopeBanner`, `DefectsToolbar`). Three readings of one loop, one of
which was never finished.

What the CLI can do and the browser cannot:

| Capability | CLI | HTTP route | Browser control |
|---|---|---|---|
| open / list / attribute / close / reopen | ✅ | ✅ | ✅ |
| `merge <dup> --into <keeper>` | ✅ | ❌ | ❌ |
| `fix-item --task / --create / --clear` | ✅ | ❌ | ❌ (option B) |
| `list --with-fix-state` | ✅ | ❌ | ❌ |

`web/blueprints/defects.py` even names "the merge remedy" as a 409 case in its
docstring — a conflict the API can return and no route can produce.

**Triage for the 19 unattributed defects.** Unattributed is a first-class
state, not a gap to guess at, so the model is Linear's Triage inbox: a queue
the operator routes from, never a classifier that assigns a parent. A wrong
parent inflates one feature's rounds and exonerates another.

Invariants:

1. Every CLI capability has an HTTP route before it has a control; no control
   calls a route that does not exist.
2. Triage never infers `feature_id` from suspect paths, reporting session, or
   cost. The operator declares it or it stays null.
3. Merging is non-destructive in the ledger sense: the duplicate's suspect
   paths union into the keeper, and neither row's `blamed_event_id` is rewritten.

```gherkin
Scenario: the merge remedy is reachable from the browser
  Given two open defects with the same symptom against one feature
  When I merge the duplicate into the keeper from the defect page
  Then the keeper carries the union of both suspect paths
  And the duplicate reads closed as a duplicate

Scenario: triage routes an unattributed defect without guessing
  Given a defect with feature_id NULL and a suspect path under lib/features/
  When I open the triage queue
  Then no feature is preselected
  And the defect stays unattributed until I declare one
```

## Option G, in detail — retire the doc sprawl

Three documents describe one subsystem:

| Doc | Lines | Status |
|---|---|---|
| `docs/feature-pipeline.md` | 277 | self-labelled *"A design proposal… delete this file once the pipeline exists."* The pipeline exists |
| `docs/feature-ledger-redesign.md` | 533 | accurate, spec-notation, 12 invariants, names real test files |
| `~/.claude/skills/feature-pipeline/SKILL.md` | 158 | accurate operator-facing; its frontmatter still says "spec in the tracker… ship by moving the issue", which its own body contradicts |

Known contradictions in `feature-pipeline.md`: a defect's address is
`features.id`, not a key (the key was dropped in `0028`); `lib/defects/rounds.py`
is retired for native features and survives only for Linear; no tracker read is
on the rounds path; run-lane provenance is never mentioned; the entire
slice/transcribe/DAG half of the subsystem is absent; and `:65` claims revision
history is available from a tracker that native features do not have.

The fix is to fold the surviving decisions into `ARCHITECTURE.md`, delete
`feature-pipeline.md` as it instructs, and correct the skill's frontmatter.
`feature-ledger-redesign.md` keeps its invariants. Every code reference to the
deleted path (`cli/commands/feature.py:15`, `cli/commands/defects.py:20`,
`lib/defects/rounds.py:9`, `lib/trace/span_gates.py:134`,
`scripts/rework_probe.py:4`) is repointed in the same change.

## Ideas explicitly considered and rejected

| Idea | Source | Why not |
|---|---|---|
| Revision suffix in the item ID voiding inbound links (`accept~x~2`) | OpenFastTrace | Real, but it asks a human to remember to bump. Revisit if `orphaned` proves too coarse |
| Content fingerprint on the link (`sha256(text+links)`) | Doorstop | The unforgettable alternative to the above; noisy on cosmetic edits. Same fork in the road, same defer |
| Block-based spec editor | Notion, AppFlowy | Violates the stated source-fidelity decision in `FeatureSpec.vue` |
| Placeholder test result per uncovered item | Allure | Solves rendering, not storage. Option A makes it unnecessary |
| Deployment-anchored change-failure-rate | GitLab, DevLake | regin has no deployments, and the metric is weaker than rounds-to-done |
| 5-second skew buffer on regression detection | Sentry | A clock heuristic; the documented cause of its own false positives |

## Known defects in the current pipeline, not fixed by this feature

Recorded so a later option can pick them up; each is already an open defect row.

- #136 `FeatureCost.upper_bound` is a hardcoded literal, and inverted for a
  feature that is only ever secondary.
- #143 the `{value, gaps}` shape is spelled three ways across `cost`,
  `work_time` and `defects`.
- #159 `docs/feature-pipeline.md:254` sends the reader to `lib/experiments.py`
  for the kill-or-keep comparison; that module is a pattern-concealment
  experiment with no arms and no per-feature anything. **The one measurement
  the pipeline is supposed to be judged by has no framework behind it.**
- Undeclared: `defects.fix_task_id` and `feature_images.feature_id` are
  documented as foreign keys and have none in the DDL. `FeatureEvent.kind` has
  no DB CHECK. `lib/defects/tracker.py` and `lib/features/store.py:271` are
  dead. There is no `regin feature list`.

## Where the selected options live now

| Option | Feature |
|---|---|
| B — close the defect→fix loop | #92 |
| G — retire the doc sprawl | #93 |
| D — Defects tab parity (blocked on #73) | #94 |
| P — Projects tab + panel | built under #90 itself (task 168) |

Feature #63 is stale and superseded by #92: both gaps it specced have closed by
other routes. Rewrite or abandon it rather than building from its text.


## Option P, as built — projects the way Linear draws them

A project is a registered repo (`repos.id`), the id `features.project_id` and
`agent_tasks.project_id` already carry; a defect's project is its feature's.
**Nothing is stored.** Status and progress are read off feature stages, the
way a stage is read off events — a declared project status would be a second
opinion drifting from its rows.

Surfaces: a fourth, unscoped Pipeline tab `/projects` (a list: status, a
stage-segmented progress bar, features, open tasks, open defects) and
`/projects/:id|none` (header, bar, stat tiles that open the other tabs under
`?project=`, features grouped by stage, open defects, recent tasks). API:
`GET /api/pipeline/projects[/<id|none>]`, `lib/pipeline/projects.py`.

| live features (not abandoned) | any `building` | any `spec` | status |
|---|---|---|---|
| 0 | — | — | `empty` (label "Backlog") |
| > 0 | yes | — | `in_progress` |
| > 0 | no | yes | `planned` |
| > 0 | no | no | `completed` |

Progress = (`shipped` + `escaped`) / live. Escapes stay counted as done-claims
and are reported beside the bar, not by un-counting them.

Invariants:
- for every row, its feature/task/defect totals equal `lib.pipeline.counts.counts(scope)`;
  the rows (registered repos, orphan ids still named by rows, and `none`) sum to `counts('all')`.
- the panel's feature list is `lib.features.index.page` under the same scope, so
  `features.matched` equals the Features tab's total.
- passing through the unscoped Projects tab never resets the other three tabs' scope: a panel carries its own project on the strip, the list carries the last scope a scoped tab showed, then the stored one.

Deliberately not built: Linear's declared project fields — lead, target date,
milestones, health updates. Each is declared state beside a derived one and
needs a schema change; revisit only with a concrete use.

## Second survey: PM products and note products

Run 2026-09-20 after the first pass was cut short. Verified against vendor
docs, changelogs, API references and source; review-site claims are marked
UNVERIFIED in the working notes and are not relied on below.

### What regin already does right — checked, not assumed

The PM survey's headline recommendation turned out to be **already satisfied**,
and the check is worth recording so a later run does not "fix" it:

No surveyed tracker (Linear, Jira, Shortcut, Plane, Huly) has a `reopened`
*status*. All model reopening as a transition back into a non-terminal
category, recorded in an event log. **regin already does exactly this** —
`lib/features/events.py:69-74` maps `RESTARTED → STAGE_BUILDING`, so
`restarted` is an event kind and never a stage, and `rounds()` at `:160-168`
is `1 + count(restarted)` derived over an append-only log.

The fixed **category** axis those products carry beside the state name buys
regin nothing today, because `STAGES` is a hardcoded 5-tuple no user can
rename. It becomes load-bearing only if feature states ever become
user-customizable — at which point the category is what keeps the derived tabs
and `rounds()` from breaking on a rename. Recorded as a constraint on any
future custom-workflow-states feature, not as work now.

### Patterns adopted, by option

| Pattern | Source | Lands in |
|---|---|---|
| Four terminal verbs on a queue, single-key (`1` accept / `2` duplicate / `3` decline / `H` snooze); an item leaves only via one | Linear Triage; cloned by Plane Intake | #94 |
| Badge count derived from the same predicate as the list, in one transaction | Plane `#8473` is the counter-example | #94 |
| Duplicate as a reserved state that transfers the duplicate's records to the canonical, plus a banner linking back | Linear | #94 |
| Relations stored in one canonical direction with normalization (directional types swap to their reverse key; symmetric types swap so `from_id < to_id`) | Redmine `issue_relation.rb` | deferred option E |
| Tabs as saved Views over one collection, not separate routes | Linear Active/Backlog; Huly All-Issues toggle | #73 |
| One action registry rendered three ways — context menu printing its accelerator, `Cmd+K`, bare key — scoped to the selection | Linear | future |
| `base_version` → 3-way diff3 → `412` with `{base, theirs, mine}` | Obsidian Sync; RFC 9110 | **defect #176** |
| Revision cut on Joplin's dual rule (quiet >10 min, or first edit after a long gap), stored as the *previous* bytes with an author of `human` or `agent:<trace-id>`; restore is non-destructive | Joplin + Outline | declined option C — reconsider |
| Live Preview as a pure decoration layer over an unmodified buffer, `Cmd+E` to toggle off | Obsidian | future editor work |
| Slash commands as pure markdown insertion via a CM6 autocomplete source, suppressed inside code fences | Obsidian, SilverBullet, GitHub | future editor work |
| Out-of-band comment anchors (`exact` + 32-char prefix/suffix + position hint, tried in that order), with an explicit `outdated` state when re-anchoring fails | Hypothesis selectors; GitHub's null `position` | future |
| ID-anchored mentions indexed into a `spec_mentions` table, rendering a Linked-references card | Roam's "the anchor is the ID, not the title" | future |
| A fenced ` ```regin-query ` block rendered as a live table, refreshed **on expand** | SilverBullet widgets; Tana's refresh-on-expand | future |

### Two measures nobody else can ship

Every surveyed product measures time (cycle, lead, triage, age) and effort
points. **None measures money per unit of work**, because none owns the agent
sessions. `Σ cost` sliced by feature, and `cost / rounds-to-done`, have no
prior art to copy and drop straight into Linear's Insights shape — a chart is
`(measure × slice × segment)` over the view the operator has already filtered,
so there is no report builder to write.

Second: **attribution coverage belongs in a tab badge, not a chart.** The
number is 9 open unattributed defects of 20 total; the useful rendering is one
metric block with the median age of the oldest five, not a dashboard.

### Anti-patterns, with citations

Recorded so a later run does not reintroduce them.

| Do not | Evidence |
|---|---|
| Compute a badge count separately from the list predicate | Plane `#8473` — Intake count desyncs after accept/decline/duplicate |
| Ship a relation flag that enforces nothing | Huly docs admit blocking is "visual indicators only" |
| Pass a pairwise reciprocal check off as cycle detection | Redmine `#7320` (>2-issue cycles undetected), `#13914` (HTTP 500), `#8794` (infinite loop) |
| Keep filters only in the URL with no saved-view object | GitLab — requested since 2016, `gitlab-foss#18492`, `gitlab#14512`, epic `13365`, still open |
| Let a saved view hide the fields it pinned | GitLab board scope: after scoping, "you can no longer filter through these in the search bar" |
| Build an uncollapsible properties rail | Jira `JRACLOUD-76253`; users ship extensions to collapse it |
| Store link direction semantics only in the UI layer | Atlassian: semantics "only interpretable at the user interface level"; their KB documents history and panel showing opposite labels for Blocks |
| Ship burndown or velocity | velocity "is not a productivity metric… encourages gaming" |
| Use infinite scroll on a goal-directed list | NN/g: no reference points, unreachable footer |
| Replace the markdown source editor with ProseMirror/Tiptap | "ProseMirror drops what its schema can't model, by design"; Tiptap silently drops `<br>` in table cells (`ueberdosis/tiptap#7731`) |
| Follow Logseq 2.0 into a DB where files stop being hand-editable | still beta with acknowledged data-loss risk; byte-fidelity is the property regin cannot lose |
| Embed comment markers in the spec bytes | Outline's model works because Outline owns serialization; here it puts comment IDs into what the agent reads, and the first rewrite deletes them |
| Autosave the canonical column on a debounce | manufactures exactly the race defect #176 describes; keep explicit Save, put the draft in localStorage |
| Poll a transcluded query on a timer | Dataview's refresh-interval appears removed; Tana refreshes on expand — and a timer violates the repo's root-cause rule |
| Adopt a CRDT for `features.body` | Notion took one only for text *inside* a block, only once offline mode raised the risk; a CRDT hands back a merge, not the exact bytes |

