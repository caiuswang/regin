## Feature lifecycle & ledger

The feature pipeline exists to make one number trustworthy: **rounds-to-done** — one build plus every time the work was called done and walked back. Everything in `lib/features/` is shaped by that goal: a feature is a row in `features` (the population, `FROM features` with no filter), and *everything said about where it stands* is a query over the append-only `feature_events` log.

### Model

```typescript
type FeatureEvent = {                 // append-only: never UPDATE, never DELETE
  id: int                             // THE order; never `at`
  feature_id: FK<Feature>
  kind: 'specced' | 'build_started' | 'shipped' | 'restarted' | 'abandoned'
  declared_by: 'operator' | `run:${int}` | `sync:${string}`
}
type Stage = 'spec' | 'building' | 'shipped' | 'escaped' | 'abandoned'
```

### Derived figures (`lib/features/events.py`)

| Figure | Rule |
|---|---|
| `rounds` | `1 + count(restarted)` |
| `stage` | last event's kind; `shipped` becomes `escaped` if any defect's blame pointer targets it ([[defect-escape-tracking]]) |
| claimed-at | time of the last `shipped` |

### Writers

| Writer | Appends |
|---|---|
| operator `feature new` / `ship` / `abandon` | `specced` / `shipped` / `abandoned` |
| a `build`/`fix`/`task` step's run starting (`events.on_run_start`) | spec→`build_started`; building→nothing; shipped/escaped→`restarted`; abandoned→`build_started`. A `spec` step's run appends nothing |
| `feature new --ref` / `feature refresh` (`sync.py` → `events.on_sync`) | tracker state category mapped to a kind, only when it differs from the last event; an unknown word sets `sync_error` |

### Invariants

- Writing is decided by the **last event's kind alone** — no clocks, no run sequence numbers — so reporting the same run start twice is a no-op.
- A run *settling* never writes `shipped`: a finished session is not a done-claim.
- A remote refresh runs only when an operator asks; it is never a poll or triggered by a page mount.
- `lib/defects/rounds.py` is **not** on this path; it only fills a Linear snapshot field.

### Attribution & cost

`attribution.py` binds whole sessions to a feature with three provenances: `declared` (the caller's own trace), `recalled` (another trace, or from a shell), and `run` (not stored — read live through `run.trace_id → task.feature_id`). Cost (`cost.py`) is the sum of attributed sessions' `cost_usd` and is **always an upper bound**. Every figure carries its gap counters as session counts instead of a zero filled in for missing data. `metrics.py` counts a session as human exactly when no `agent_task_runs` row produced it.

### Ledger & index

`ledger.py` renders one feature's report with the stage line first and cost second. It prints the attributed session list as evidence for the upper bound. `index.py` returns a whole page from **one statement**, because a per-row `ledger.build()` measured ~1s. `steps.py` creates at most one `spec` and one `build` task per feature, keyed `feature:<id>:<kind>`. A retry is another run of the same row, not a new row.

The UI pages (FeaturesView, FeatureLedgerView) belong to [[webui-surface]]. The CLI verbs belong to [[regin-cli-architecture]].