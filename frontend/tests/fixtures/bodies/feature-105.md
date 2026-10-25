The ⌘J search panel's Sessions facet (empty query → the "Recent 12" roster from GET /api/session-switcher, `SessionSwitcherList.vue`) must include topic-proposal pipeline sessions (`origin='topic-proposal'`), ranked by recency like any other session.

Root cause (fixed in 85d39b91):
| Defect | Where | Fix |
|---|---|---|
| Proposal `session.start` emitted `repo` but no `cwd`, so `sessions.cwd` was NULL. The roster's `cwd IS NOT NULL` filter (`_candidate_rows`) dropped every proposal run. | `lib/topics/proposal_external.py::_emit_session_start` | also emit `cwd = repo` |
| Proposal spans were stamped UTC `…Z`, while hook spans are local naive. `last_seen` is ordered as text, so a proposal run sorted hours below its true recency and fell out of the top 12. | `proposal_external.py::_emit` | stamp `datetime.now().isoformat()` |

Invariants:
- A regin-emitted synthetic session's `session.start` carries `cwd`.
- Every span writer uses the local-naive basis that `sessions.last_seen` is compared in.
- `llm-stage`/`workflow` runs (the proposal's agent child) stay out of the roster by design; only the wrapper session shows.

```gherkin
Given a proposal run emits session.start for repo R
When GET /api/session-switcher is read
Then the run is listed with cwd R, above sessions last seen before it
```
Test: tests/web/test_session_switcher.py::test_a_topic_proposal_run_reaches_the_roster.

Non-goal: no shipped migration for pre-fix rows. The live DB was repaired once by hand (cwd backfill plus Z→local conversion for 30 rows; 3 pytest-leaked rows flagged is_test).
