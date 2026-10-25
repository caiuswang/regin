Cut from feature #90's survey. 0/60 defects carried a `fix_task_id` and 0 tasks had `source='defect'` — the loop had never run end to end. The CLI was fully implemented, `DefectRow.vue:52-56` already rendered the resulting link and `DefectsView.vue` already scoped the list by fix item, so the read surface shipped for data nothing wrote. Missing: `POST /api/defects/<id>/fix-item` and a browser control. Feature #63 specced this and is stale — both its stated gaps closed by other routes (POST /api/defects exists; parent_id dropped in migration 0028; lib/tasks/integrations/regin.py no longer exists); it is folded in here.

## What was built

One shared verb, one route, one control, one derived reading.

```
lib/defects/fix_items.py   create_for(defect_id) -> Defect     # authors the task, attaches it
                           back_pointer(row) -> str            # the task body, and all of it
POST /api/defects/<id>/fix-item   {"task_id": N} | {"create": true}
```

`create_for` and `back_pointer` live in `lib/defects/` rather than in the CLI
because the browser now asks for the same task: two copies would let the board
and the ledger describe one bug in two wordings, which is the thing storing the
symptom verbatim exists to prevent. The body carries the defect id, the
reporting session, the suspect paths and — new — the `missing check`, because
that is the one line of a defect that describes *future* work and the task is
where that test gets written.

The route takes exactly one of `task_id` and `create`; they are two different
assertions and a body naming both has no single answer. There is deliberately
**no `clear`**: the pointer is last-writer-wins, so a browser that mis-pointed
re-points, and unpointing a defect entirely stays a CLI correction
(`regin defects fix-item --clear`).

On the page, closing a defect raises an **offer** quoting the check back —
never on arriving at an already-closed row, and never when a fix task is
already pointed at. Accepting posts `{create: true}` and the row renders its
`task #N` link without a reload.

## The reading: a named check is a claim, not a test

Sentry splits what regin conflated: `Fixes SENTRY-317` in a commit
**references but does not resolve**; resolution fires only when a release
containing that commit exists. **claimed → shipped → stayed-fixed**, three
states, not one boolean.

`missing_check` is the claim. The ledger now reads it against the checks the
repo actually holds, on read, with **no new column** (`lib/defects/checks.py`,
surfaced as `defects.checks` and `defects.gaps` on `GET /api/features/<id>`
and as a `checks` line in `regin feature ledger`).

A claim is spelled in whichever suite would have caught the bug, so four
spellings are read, **most specific first**:

| kind | spelled as | redeemed by |
|---|---|---|
| pytest function | `test_the_badge_matches` | a `def test_…` under `testpaths` |
| pytest module | `tests/x/test_web_list.py` | a `test_….py` file there |
| spec title | `'the rail keeps its width'` | a `test('…')` title under playwright's `testDir` |
| spec file | `nav.spec.js`, or its full path | a spec of that name in the same `testDir` |

Only the most specific kind *present* is judged, and that ordering is
load-bearing. The ordinary spelling is the pytest node id
`tests/defects/test_ledger.py::test_a_case_nobody_wrote` — module and function
in one string. Credit the module and every such row reads `check written` while
the function does not exist, which is exactly the rubber stamp this reading
exists to stop.

**Every** named check must be redeemed, not one of them: a row naming two
checks that got one is half-redeemed, and half is not written. And a quoted
run is a *title* — a literal, not a reference — so a `.py` inside one is part
of the title's words, never a module claim that outranks it.

A file-level claim cannot reach `check written`, which is the fourth reading:

| most specific claim | every one redeemed | reads |
|---|---|---|
| none | — | `no check named` |
| function or title | no | **`check not yet written`** |
| function or title | yes | `check written` |
| module or spec file | no | **`check not yet written`** |
| module or spec file | yes | **`check file exists, name unverified`** |

"The regression belongs in `tests/features/test_ledger.py` but nobody ever
wrote it" names a file that exists and a check that does not. Reading that as
written is the same rubber stamp one rung down — measured on the live ledger,
file-only redemption was stamping 6 of 32 written rows. The three non-written
readings are the defects figure's three gap counts; none is ever counted as
written.

```gherkin
Scenario: a named check that was never written is visible
  Given a closed defect whose missing_check names "test_the_badge_matches_the_queue"
  And no test of that name exists
  When I read the feature's ledger
  Then the defect reads "check not yet written"
  And it is counted in the gap line, never as a written check

Scenario: a node id is a claim about its function, not its module
  Given a closed defect whose missing_check is "tests/x/test_ledger.py::test_absent"
  And tests/x/test_ledger.py exists but defines no test_absent
  Then it reads "check not yet written"

Scenario: two of the 48 say only "fixed"
  Given a closed defect whose missing_check is "fixed"
  Then it reads "no check named"

Scenario: a Playwright title is a check too
  Given a closed defect naming "rail.spec.js :: 'the rail keeps its width'"
  And a spec declares test('the rail keeps its width')
  Then it reads "check written"

Scenario: a file that exists is not a check that was written
  Given a closed defect whose missing_check names only "suite/test_things.py"
  And that file exists
  Then it reads "check file exists, name unverified"
  And it is counted in the gap line, never as a written check

Scenario: half redeemed is not redeemed
  Given a closed defect naming "test_present and test_absent"
  And only test_present exists
  Then it reads "check not yet written"
```

### The oracle, and the decision that was reversed

This spec originally said the match would be *"by test-name lookup against
`pytest --collect-only`, the same zero-execution oracle Option A would have
used"*. Both halves of that turned out wrong and the code now does something
else:

- **`pytest --collect-only` is too slow to sit inside a ledger read.** Measured
  in this repo: 2.9 s for collection against 40 ms for a source scan, and the
  reading is computed inside every `GET /api/features/<id>`. A three-second
  subprocess there is paid by a page load. The oracle reads the test *sources*
  instead, with the roots taken from `pyproject.toml`'s `testpaths` and
  `frontend/playwright.config.js`'s `testDir` so it is not a hardcoded
  directory.
- **Pytest alone is the wrong oracle.** Measured against the live ledger, a
  pytest-only reading called **20 of 48** closed defects "no check named" when
  they plainly named a Playwright title — a false reading, and the loudest one.
  Playwright is the other half of this repo's checks and had to be read too.

The cost, stated: a check that only exists after collection — a hook-built
parametrization, a title a helper composes — reads as not written, and a title
quoted beside an apostrophe (`the run controls' line`) can be sliced wrong and
read the same way. Both errors point the same direction on purpose: this
reading is a flag to go and check, and a false "not written" costs a look while
a false "written" is the rubber stamp.

Do **not** copy Sentry's 5-second `active_at` buffer in `_handle_regression` —
the one clock heuristic in an otherwise identifier-based design, and the
documented cause of its false regressions under event latency.
