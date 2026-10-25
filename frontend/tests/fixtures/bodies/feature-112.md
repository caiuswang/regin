# Attribute all current defects

## Intent

The ledger has open defects that belong to no feature. Nothing counts them: they
add to no feature's rounds or escapes, and no fix work picks them up. Sort every
one into exactly one terminal outcome, so that `regin defects list` (the
unattributed view) shows **no open rows**. This is a one-time ledger sweep
done with the verbs that already exist. It changes no code.

## Non-goals

- **Open defects already attributed** (7 rows at spec time: #160 #169 #170 #172
  #173 #174 #177). Their features own them. The sweep does not re-attribute
  them or check them for staleness.
- **Closed unattributed defects** (11 rows: #120 #128–#135 #137 #148). They are
  already resolved. Attributing them now would mint escapes after the fact.
- **Fixing any defect.** A new feature created here is only `specced`. The sweep
  arms no tasks and makes no code edits.
- **New CLI or API surface.** If a verb is missing, record it as a defect against
  #112 with `regin defects open … --feature 112 --session <sid>`. Do not build it
  in this run.

## Shape

```
DefectVerdict =
  | Closed      { check: str }              -- fixed or gone; `defects close --check`
  | Merged      { into: DefectId }          -- duplicate of another open defect; `defects merge --into`
  | Attributed  { feature: FeatureId,       -- `defects attribute <id> <feature>`
                  existing: bool,           -- false ⇒ created by `feature new --spec` in this run
                  evidence: str }           -- commit sha / session id (existing) or grouping rationale (new)
  | Declined    { reason: str }             -- not a defect; web-only verb (POST /api/defects/<id>/decline)

TriageSet = { d ∈ defects | d.status = 'open' ∧ d.feature_id IS NULL }
  -- read at build START. It is not frozen to the spec-time list, because
  -- "all current" means what is open when the sweep runs.
```

Spec-time snapshot of the TriageSet (10). The build re-reads it and must also cover any row added since:

| id | area | suspect paths | spec-time note (unverified) |
|---|---|---|---|
| 136 | feature cost | lib/features/cost.py, ledger.py | `upper_bound: bool = True` still at cost.py:83 |
| 143 | feature ledger | lib/features/cost.py, ledger.py | same files as #136 |
| 138 | task lifecycle | lib/tasks/store.py, grain.py | — |
| 139 | task lifecycle | lib/tasks/runs.py, lib/agent_sdk/store.py | — |
| 140 | task lifecycle | lib/orm/models/tasks.py, lib/tasks/… | — |
| 141 | session model | db/schema.sql, lib/orm/models/trace.py | — |
| 142 | session aggregates | lib/trace/merge.py | — |
| 159 | docs | docs/feature-pipeline.md, lib/experiments.py | the doc is deleted, but the claim moved into ARCHITECTURE.md |
| 175 | hook tests | hook_manager/tests/test_handlers.py | pre-existing test failure on master |
| 176 | feature spec API | web/blueprints/features.py | `set_body` still unconditional at features.py:155 |

## Rules

Decision table. Evaluate the rows top to bottom and take the first that matches:

| # | condition (established by evidence, not by reading the symptom) | verdict |
|---|---|---|
| R1 | Not a defect: works as designed, or a misreport | Declined (reason) |
| R2 | Gone: the code or doc it names was removed | Closed. The check names what would have caught it at the time |
| R3 | Fixed: the reproduction no longer fails on the current tree | Closed. The check names the test that now covers it, if one exists |
| R4 | Duplicate of another open defect (attributed or not) | Merged into the older row |
| R5 | Still reproduces, and an existing feature's work **introduced** it | Attributed to that feature |
| R6 | Still reproduces, and no existing feature introduced it | Attributed to a **new** feature created for it |

- **Match means causation, not topic (R5).** A feature matches only when its
  commits or sessions introduced the suspect code. The evidence is `git log -L`
  or `git blame` on the named lines, tied to a commit made in that feature's
  sessions or tasks. Topical overlap is not a match. Attributing to a
  `shipped` feature records an **escape** against its current ship claim
  (bbe6e94d). A wrong match mints a false escape, so without a causal commit
  the rule falls through to R6.
- **New features are grouped (R6).** Create one feature per cohesive fix scope,
  not one per defect. Defects that share a subsystem and would be fixed together
  go under one feature, for example #136 + #143 (feature cost/ledger) or
  #138 / #139 / #140 (task lifecycle). Create each with
  `regin feature new "<title>" --spec "<thin prose naming the defect ids>"`.
  Its spec contains no file paths, per the `feature new` contract.
- **Fixed and gone are proven, not assumed (R2/R3).** Evidence is one of:
  - a named test run green on the current tree
  - a `file:line` read showing the named behaviour is gone
  - the commit that deleted the path

  "Probably fixed" is not an R3.
- **`close` always carries a real check.** `missing_check` is the ledger's only
  field that changes behaviour, and `lib/defects/checks.py` reads it back. Spell
  it as a pytest node id or a Playwright spec title. Never put "obsolete",
  "duplicate" or "not a bug" there. Those cases are R1 or R4.
- **Every verb that takes `--session` gets `<this session's id>`.** That makes
  the sweep traceable to the run that made each call. That includes `feature
  new`, which records the session on the new feature's `specced` event. Pass
  `--project <repo>` as well, so the feature is filed under a project.
- **Gone means the claim is gone, not the path (R2).** A deleted file whose
  claim was carried into another file still reproduces. Follow the claim before
  closing. A defect making two claims is gone only when both are.
- **A feature tag in a commit subject is not causation (R5).** The introducing
  commit must come from a session on that feature's ledger. A bulk landing that
  names a feature but was committed from an outside session falls through to R6.
- Rows outside the TriageSet are not touched.

## Invariants

- I1: After the sweep, `TriageSet = ∅`.
- I2: Each row that was in the TriageSet at start has exactly one verdict.
- I3: Every row outside the start TriageSet keeps its `status`, `feature_id`,
  `missing_check` and `triage_state`.
- I4: Every feature created by the sweep has ≥ 1 defect attributed to it.
- I5: The run's git diff is empty. Ledger writes only.

## Acceptance

```gherkin
Scenario: no open defect is left unattributed
  Given the sweep has finished
  When  I run `sqlite3 db/regin.db "select count(*) from defects where status='open' and feature_id is null"`
  Then  it prints 0
  And   `regin defects list` shows no row whose status is open

Scenario: every start-set defect has exactly one recorded verdict
  Given the TriageSet ids captured at build start
  Then  each id is closed, or attributed and open, or absent (merged)
  And   the task-finish report has one row per id: verdict, target, evidence

Scenario: closures name a check the oracle can read
  Given a defect the sweep closed
  Then  its missing_check is a pytest node id or a Playwright spec title
  And   it does not contain "obsolete", "duplicate" or "not a defect"

Scenario: an attribution to an existing feature is causal
  Given a defect the sweep attributed to a feature that existed before the sweep
  Then  the report cites a commit sha from that feature's work that touched the defect's suspect lines

Scenario: new features are grouped and non-empty
  Given the features created during the sweep
  Then  each has ≥ 1 attributed defect and a spec naming its defect ids
  And   no two new features cover the same subsystem

Scenario: nothing outside the start set moved
  Given a snapshot of the defects table taken at build start
  When  I diff it against the table after the sweep, excluding start-set ids
  Then  the diff is empty

Scenario: a deleted path whose claim moved is not "gone"
  Given docs/feature-pipeline.md does not exist on the current tree
  And   ARCHITECTURE.md still says lib/experiments.py runs the pipeline comparison
  Then  #159 is not closed as R2
  And   it is attributed to the feature whose session carried the claim across (#93, 5a2ccf2e)
```
