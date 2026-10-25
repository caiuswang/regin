Two pages and one line. A **feature index** at `/features` listing every feature key regin knows, a **feature ledger page** at `/features/:key` showing which sessions built it, what they cost and what escaped, and an **attribution line** in the session trace header saying what the session you are reading built — and letting you declare it.

The CLI half shipped as [Feature ledger — the sessions that built a feature, what they cost, and what escaped](<https://linear.app/caiuswang/issue/CAI-242>) (`ceec2ab7`). This is the window over those numbers. The design was charted as [Feature ledger in the browser](<https://linear.app/caiuswang/issue/CAI-243>) across nine decision tickets; every rule below is traceable to one of them, and the grouping in **Acceptance** names which.

## Vocabulary

* **Feature key** — a tracker issue identifier (`CAI-242`). A feature *is* a Linear issue; regin holds a read-through cache, never a copy.
* **Attribution** — the assertion that a session built a feature. It is always an operator act, never an inference. Three provenances: `declared` (you attributed your own trace), `recalled` (you attributed another, or declared from a shell or a browser with no trace of its own), `task-link` (a task you linked to the issue launched the run).
* **Unattributed** — the *absence* of a row. Never a bucket, never a key, never a zero.
* **Gap** — a count of sessions a figure could not see: `no-usage-captured`, `traceless runs`. A gap is never a substituted zero and always rides with the figure it qualifies.
* **Stage** — where the tracker says the feature is. `null` means no tracker answered; it is not `spec`, and `unknown` is never a stage name.
* **Freshness** — `stage_synced_at`. `null` is never-fetched; a timestamp is as-of-T. There is no third state, because a failed fetch is not persisted.
* **Coverage** — the share of all sessions and all spend that any attribution accounts for. Today: 13 of 2935 sessions, $57.24 of $23,251.49.

## What it refuses to know

The refusals are the design. Each one is a rule a reviewer can check.

1. **It never infers an attribution.** No cwd heuristic, no branch convention, no top-N-by-cost sweep, and no filter on the index that would hide a key the coverage line counts.
2. **No web read touches the tracker.** A ledger read is local-only; the stage comes from the issue cache. An unreachable tracker is a **200 with** `stage: null`, the HTTP analogue of `feature ledger` exiting 0. Refreshing is a separate, explicit event. No web read calls `store.fix_states()` — not behind a flag, not on demand.
3. **No clocks.** No TTL on the cache, no poll, no debounce, no skew window. Freshness is stated, never acted on.
4. **It never moves a tracker issue's state.** The board must not flap.
5. **Cost is an upper bound, permanently** — and the bound rides *in the glyph* (`≤ $41.90`), not in a tooltip or an 11px caption. A crop of the numeral alone must still read as a bound.
6. **No figure is ever withheld by role.** Field-level redaction has no precedent in regin, so a withheld figure could be mistaken for a gap. Role changes affordances in the component, never the payload — there is no `can_open` field.
7. **An empty ledger never renders a zero.** `none declared` is a dashed block naming the wrong reading; absent figures *shrink* to italic faint rather than 28px bold.
8. **The cache feeds display, never a decision.** `blame_state` never reads it, and the CLI never falls back to it, so `feature ledger` stays behaviourally unchanged.
9. **Two surfaces never disagree about one fact.** One issue cache, one stage, one freshness statement, one provenance wording.

## Surface

**Routes** — `/features` (index) and `/features/:key` (ledger), mirroring `/skills` + `/skills/:id`. One nav entry, *Features*, under **Engineering**, with **no role conditional**.

**Endpoints**

| Method | Path | Gate |
| -- | -- | -- |
| `GET` | `/api/features` | `@require_auth` |
| `GET` | `/api/features/<key>` | `@require_auth` |
| `POST` | `/api/features/<key>/attribute` | `@require_editor` |
| `DELETE` | `/api/features/<key>/attribute/<trace_id>` | `@require_editor` |
| `POST` | `/api/features/<key>/stage/refresh` | `@require_editor` |
| `GET` | `/api/sessions/<trace_id>/features` | admin (via `ADMIN_API_ENDPOINTS`, like its siblings) |

`GET /api/features` is a keyed object `{items, coverage, unattributed_defects, total}` taking exactly one query parameter, `q`, matched server-side over key and cached title (`_like()` escaping and `Q_MAX` from `lib/tasks/query.py`). No cursor, no filter controls. `GET /api/features/<key>` is a thin wrapper over `Ledger.to_dict()`, which is already the wire shape.

**Read gate**: reads are authenticated-any-role because regin's role split is about write-accidents, not read-secrets — `GET /api/grades` already serves a viewer per-session `cost_usd` and fleet totals, so gating a per-feature slice of numbers a viewer can already sum would protect nothing. The refresh is `@require_editor` because it writes the shared cache and spends someone else's quota; this mirrors the existing `GET …/links` / `POST …/refresh` split in `tasks.py`.

**Lib**

* `lib/features/index.py` — the three-table union (`session_features` ∪ `defects.feature_key` ∪ `agent_task_links.external_key`), one query for the whole page, `cost.coverage()` computed once per page. Precedent: `coverage()` itself lives in lib, and the union is ledger reasoning, not transport.
* `lib/features/attribution.py` — add `attributions_for_session(trace_id)`, the merged reverse lookup (declared rows ∪ task lane), and `unattribute(feature_key, trace_id)`. `features_for_session` **and** `declared_feature` **are not touched**: display needs the truth, blame needs a claim.
* `lib/features/ledger.py` — `build()` takes a stage resolver, defaulting live, so "the ledger endpoint never calls `fetch_issue`" becomes a test name rather than a convention.

**Schema** — one Alembic revision `alembic/versions/0024_issue_cache.py` (head is `0023_feature_ledger`), the matching `db/schema.sql` change, and the SQLModel in `lib/orm/models/tasks.py`. A schema change is always these three edits or fresh and migrated installs diverge.

The new table is a **feature-keyed issue cache**, keyed `(provider, external_key)`, holding the whole normalized `ExternalIssue`. Today's cache is keyed by the wrong thing: `snapshot_json` hangs off a *link row*, and measured on the live DB 5 of ~15 feature keys have no link row while `CAI-219` has two. `agent_task_links.snapshot_json` **and** `agent_task_links.synced_at` **are backfilled into the new table and dropped in the same revision** — two writers over one fact would let `TaskLinkCard.vue` and the ledger show different stages for the same issue. Every successful fetch writes it, from CLI or web; only the web reads it.

## Pages

**The ledger page.** The stage leads as a *line*, not a band — a full-width stage card was the largest object on the empty ledger most features will be for a long time, which inverts the framing job. Four figure tiles, each carrying its own gap counter **in-tile**, with a conditional escalation rail that fires only where a gap count exceeds the count it qualifies. When gaps outnumber the figure they become **ghost rows** in the evidence list — one hatched row per gap type with a `×N` multiplier — and the header's gap counter must reconcile with the rows beneath it. Coverage sits between the figures and the evidence with a proportional track. Session rows read `date · trace_id[:8] · title`, the 8-char prefix ported verbatim; the short id is itself the link into the trace, **unlinked but never omitted** for a non-admin. The provenance tag links back: `task #44` → `/tasks/44`. Un-declare lives on the row.

**The index.** One row per key, unfiltered, ordered **most recent activity descending** — the surface is a way in, not a leaderboard. Every other candidate ordering ranks a gap against a figure, which is `none declared` rendered as `$0.00` by an `ORDER BY`. The row carries stage and title from the cache (a join, free). A row without a stage is the most informative row in the list: the stage-less keys are exactly those with bugs blamed on them and no attribution. Header counters — coverage, and unattributed defects in gap grammar — describe the **population, never the match**, so the page reads `3 of 25 features`. Search is one input, submitted on Enter, never per keystroke, pushed to `/features?q=`: a shared cursor links to a position that moves, a shared `?q=` links to a predicate.

**The trace header line.** Full state only; the collapsed header keeps identity and digest. Reads `attributions_for_session`, so it cannot disagree with the feature page — measured, 22 of 23 attributed traces are task-lane-only and a declarations-only read would call all 22 unattributed. The line reads `feature · CAI-242 · via task #44 ↗`; the word "task" is never bare, because it already means the session's TodoWrite list on this screen. **Un-declare is absent — never disabled — on a task-lane row**, since there is no row to delete and the tag points at `/tasks/44` where the assertion can actually be withdrawn. Declaring from here is the *primary* gesture and reuses `GET /api/features` unchanged as its picker. A second feature is allowed, with `is_primary` an explicit checkbox defaulting false; that two declarations break `regin defects blame @session` is stated as a note, not enforced.

**The task-link card.** Gains one dedicated `ledger →` link, present for every link row regardless of stage, beside — not in — the mutation cluster. The stage chip takes no `to` prop on any surface: it is absent exactly when the ledger is most worth opening. The card reads the issue cache for stage *and* freshness, so `never synced` becomes the shared `stage not fetched`.

## Acceptance — tests that do not yet exist

Grouped by the ticket that decided each.

[CAI-244](https://linear.app/caiuswang/issue/CAI-244) **— declaring from the browser**
`tests/features/test_web_attribute.py`

* `test_browser_declaration_is_recalled`
* `test_browser_declaration_leaves_round_null`
* `test_browser_declaration_leaves_feature_attributed_span_unchanged`
* `test_attribute_refuses_trace_absent_from_sessions_as_4xx`
* `test_is_primary_defaults_false_and_is_never_a_side_effect`
* `test_unattribute_deletes_the_row`
* `test_unattribute_emits_a_feature_unattributed_span`
* `test_unattribute_of_absent_row_is_a_named_4xx`
* `test_actor_lands_in_the_activity_log_not_the_table`

[CAI-245](https://linear.app/caiuswang/issue/CAI-245) **— the read never touches the tracker**
`tests/features/test_web_ledger_read.py`

* `test_ledger_endpoint_never_calls_fetch_issue`
* `test_ledger_endpoint_never_calls_fix_states`
* `test_unreachable_tracker_returns_200_with_stage_null`
* `test_stage_comes_from_the_issue_cache`
* `test_failed_fetch_does_not_persist_freshness`
* `test_stage_synced_at_rides_beside_stage_not_inside_it`
* `test_cli_never_reads_the_cache`
* `test_every_successful_cli_fetch_warms_the_cache`
  `tests/orm/test_issue_cache_migration.py`
* `test_0024_backfills_snapshot_json_into_the_issue_cache`
* `test_0024_drops_agent_task_links_snapshot_json_and_synced_at`
* `test_schema_sql_matches_alembic_head`

[CAI-246](https://linear.app/caiuswang/issue/CAI-246) **— who may read, who may declare**
`tests/web/test_features_auth.py`

* `test_viewer_may_read_a_ledger`
* `test_viewer_may_not_declare`
* `test_viewer_may_not_refresh_the_stage`
* `test_payload_is_identical_for_every_role`
* `test_non_admin_gets_session_rows_without_the_trace_link`
* `test_no_can_open_field_in_any_payload`

[CAI-247](https://linear.app/caiuswang/issue/CAI-247) **— what the ledger page shows**
`frontend/tests/feature-ledger.spec.js`

* `stage renders as a line, never a full-width band`
* `cost renders the bound in the glyph, not a tooltip`
* `each gap counter renders inside the tile it qualifies`
* `the escalation rail fires only where a gap exceeds its figure`
* `gaps outnumbering the figure render as ghost rows with a multiplier`
* `the header gap counter reconciles with the ghost rows beneath it`
* `an empty ledger renders none declared, never $0.00`
* `an absent figure shrinks to italic faint, never 28px bold`
* `a zero-token block collapses to an em dash`
* `stage null renders no chip at all`
* `an in-flight refresh keeps the last-good timestamp`
* `a failed refresh is an inline note, not a toast`
* `the session row short id links into the trace`
* `un-declare is reachable on the session row`

[CAI-248](https://linear.app/caiuswang/issue/CAI-248) **— the index and enumeration**
`tests/features/test_index.py`

* `test_union_includes_keys_known_only_through_defects`
* `test_union_includes_keys_known_only_through_task_links`
* `test_index_is_one_query_regardless_of_row_count`
* `test_coverage_is_computed_once_per_page_not_once_per_row`
* `test_index_never_calls_ledger_build`
* `test_unattributed_defects_is_a_counter_never_a_row`
* `test_ordering_is_most_recent_activity_descending`
* `test_null_cost_never_sorts_as_cheapest`
  `frontend/tests/feature-index.spec.js`
* `a row without a stage still renders as a row`
* `the unattributed-defect counter is not a link`

[CAI-253](https://linear.app/caiuswang/issue/CAI-253) **— the task-link card**
`frontend/tests/task-link-stage.spec.js` *(must keep passing)*
`frontend/tests/task-link-ledger.spec.js`

* `every link row carries a ledger link regardless of stage`
* `the stage chip takes no link on any surface`
* `the card renders cache freshness, not link synced_at`
* `never synced reads as stage not fetched`

[CAI-255](https://linear.app/caiuswang/issue/CAI-255) **— search, not filters or pages**
`tests/web/test_features_query.py`

* `test_q_is_matched_server_side_over_key_and_cached_title`
* `test_q_escapes_like_wildcards`
* `test_q_longer_than_q_max_is_refused`
* `test_counters_describe_the_population_not_the_match`
* `test_no_cursor_parameter_is_accepted`
  `frontend/tests/feature-search.spec.js`
* `search submits on Enter, never per keystroke`
* `a submit pushes q into the URL`
* `no match names the population`

[CAI-256](https://linear.app/caiuswang/issue/CAI-256) **— the trace view**
`tests/features/test_attributions_for_session.py`

* `test_merged_lookup_includes_task_lane_traces`
* `test_features_for_session_is_unchanged`
* `test_declared_feature_is_unchanged`
* `test_trace_view_and_feature_page_agree_on_every_attributed_trace`
  `frontend/tests/trace-attribution.spec.js`
* `an unattributed session renders none declared in the header`
* `the line renders only in the full header state`
* `the provenance tag never renders the word task bare`
* `a task-lane attribution offers no un-declare and no disabled control`
* `a declared attribution offers un-declare inline`
* `the picker reads GET /api/features`
* `is_primary is an unchecked checkbox by default`
* `a second declaration renders the @session note`

## Fixtures

`tests/defects/test_tracker_rounds.py:84-91` is the precedent for an unreachable tracker — `monkeypatch` on `settings.task_integrations.linear_enabled` and `linear_api_key`. The suites to extend are `tests/features/{test_attribution,test_cost,test_ledger,test_recalled}.py`. Playwright specs live under `frontend/tests/`; `task-link-stage.spec.js` is the one that must keep passing unchanged.

## Corrections found while charting

* `TaskLinkCard.vue`'s `STAGE_COLOR` maps `escaped: 'amber'` and `.badge-amber` is defined nowhere in `style.css`, so the one stage the component calls "unmissable" renders with no colour today. Filed as [CAI-252](https://linear.app/caiuswang/issue/CAI-252).
* `/api/sessions/<id>/subtrees` and `/api/sessions/<trace_id>/grades` carry no decorator and are absent from `ADMIN_API_ENDPOINTS` while their 20 siblings are in it. Filed as [CAI-251](https://linear.app/caiuswang/issue/CAI-251).
* `features_for_session` reads only `session_features` while `sessions_for_feature` merges the task lane. Not a bug in the CLI — blame wants a claim — but it is why `attributions_for_session` exists.

## Known limits

* **Coverage is 0.4%.** The surface will mostly render near-empty ledgers, which is the point, not a defect to design around.
* **794 sessions (27%) carry no usage rows at all** — 99 of the last 213, so it is live, not legacy. It will be the loudest number this view shows, and fixing it is a separate effort.
* `round` **is NULL on every browser declaration.** If the browser becomes the dominant path, the per-round slice becomes a CLI-only artifact.
* **The trace view is admin-only**, so its declare — the primary gesture — is reachable only by admins. The feature page's form remains the editor's path.