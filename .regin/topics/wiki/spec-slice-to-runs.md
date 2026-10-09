## Spec → slices → task runs

The **slice table inside `features.body` is the plan.** There is no draft entity and no approval record. Each step below mechanically transcribes a plan a human wrote, and refuses whatever it cannot read without guessing.

### Flow

| Step | Command / module | Rule |
|---|---|---|
| slice | `regin task slice-check` · `slicing.py` + `slice_table.py` | refuses a spec with no falsifiable acceptance item; refuses a schema change split across two slices |
| tail | `regin task tail` · `tail.py` | appends integrate → migrate → verify rows rendered from `.regin/commands.json`; a `null` command renders its stated reason |
| transcribe | `regin task transcribe` · `transcribe.py` | table → `agent_tasks` + `agent_task_deps`, keyed `(feature_id, "S<n>")` via `(source, external_id)`; ambiguous rows refused by name; returns proposed arm params, never arms |
| arm | `arm.py` | the **only** place execution params enter a task; a human act; allowlist mirrors `supervisor.launch_run` |
| run | `runs.py` + `deps.py` | dependents are recomputed by the completion that could unblock them. There is no scheduler and no interval |
| settle | `runs.py` · `finish_run` | the agent's own `task finish` report decides; a session that ends without one settles `failed`. Feature-level correctness is the done-claim → defects chain's job, not a shell gate's |

### Invariants

- **Task kind is declared** (`spec | build | fix | task`), orthogonal to status, and is never inferred from a title.
- **Dependency edges:** `requires` waits for the dependency to succeed; `after` waits for it to settle either way. Cycles are rejected at insert, so readers carry no visited-guards.
- **Settling once:** two completions can settle a run. `finish_run` is the agent's `task finish` signal and `handle_completion` fires when the session ends. Both go through `complete_run`, which is idempotent, so neither double-fires dependents. The terminal write happens in a `finally`.
- **Liveness:** a reader merges the row's claim with `agent_runs` liveness. A row is never condemned on pid inequality alone.
- **Command table** (`commands.py`): `test`, `migrate` and `e2e` must all be present. A `null` value needs a matching `reasons` entry, and an absent key is refused, so a deliberately missing command stays distinguishable from a forgotten one.

A run *settling* never ships a feature. Run starts feed the event log described in [[feature-lifecycle-ledger]]. Task capture, integrations and the tasks UI are outside this page.