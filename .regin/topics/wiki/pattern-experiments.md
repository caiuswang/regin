# Pattern Conceal Experiments

The experiments feature lets a regin operator hide one or more `##` / `###` sections from a pattern's `SKILL.md` *before* it is deployed as a Claude Code skill, so downstream rule-trigger rates can be compared against the unconcealed baseline. It is the only place in regin where a pattern's deployed body intentionally differs from the source on disk.

This is the only "experimental" subsystem in the repo. The `Early beta` label in `README.md` is a project-wide status disclaimer, and `lib/skills/skill_router.py` carries an `(experimental)` annotation but is a single inference wrapper, not a topic.

## Data model

- **`experiments` table** — defined in `db/schema.sql` and modelled as the `Experiment` SQLModel in `lib/orm/models/rules.py`. Columns: `pattern_slug`, `name`, `conceal_spec` (JSON list of `##` / `###` heading strings), `active` (0/1, enforced one-active-per-pattern at the application layer in `lib/experiments.py::activate`), `created_at`, `activated_at`. Indexes: `idx_experiments_pattern`, `idx_experiments_active`.
- **`rule_triggers.experiment_id`** — a nullable column on the trigger log (`db/schema.sql`, indexed via `idx_rule_triggers_experiment`) used by the rollup endpoint to separate baseline (`IS NULL`) from variant samples. The ingest path in `web/blueprints/rules/triggers.py::api_ingest_rule_trigger` (called by `hook_manager/handlers/rule_check.py` via `POST /api/rule-triggers`) does **not** include `experiment_id` in the inserted row; the column is read by `_rollup` but written only by paths that populate it explicitly. Interpret variant counts on fresh data with that gap in mind.

## Conceal filter

`lib/experiments.py::apply_conceal(body, sections)` is a pure function. It walks the markdown line-by-line and, for every heading whose exact text matches an entry in the conceal spec, drops all lines from that heading up to (but not including) the next heading of equal-or-higher level. `## Foo` therefore strips nested `### Bar` children with it; `### Bar` strips only itself.

`list_sections(pattern_slug)` enumerates `##` / `###` headings from the pattern's on-disk `SKILL.md` (frontmatter excluded) to populate the conceal-spec checkboxes in the UI.

## Deploy-time integration

The conceal is applied at deploy time, not at activation time. Exactly one call site applies it:

- **Global skill deploy** — `lib/skills/skill_deployer.py::deploy_pattern_as_skill` reads `experiments.get_active(procedure_id)` after copying the pattern source into the active provider's skills directory, applies `apply_conceal` to the body, then writes `content.md`. The `SKILL.md` shim points at `content.md`.

`lib/skills/skill_sync.py::push` calls `deploy_pattern_as_skill` and also recognises an active experiment as a legitimate reason for the deployed copy to drift from source: when a force push is required, the message names "an active concealment experiment" instead of "unmerged edits in the deployed copy".

## Lifecycle

1. **Create** — `experiments.create(pattern_slug, name, sections)` (called from `POST /api/experiments`) inserts an inactive row.
2. **Activate** — `experiments.activate(id)` flips `active=1`, deactivates every other experiment on the same pattern in the same transaction, and returns the pattern slug. The Flask handler then resolves the skill id via `skill_registry.skill_id_for_procedure` and calls `lib.skills.skill_sync.push(skill_id, force=True)` to re-deploy with the new conceal applied.
3. **Edit while active** — `POST /api/experiments/<id>/edit` rewrites name/sections; if the row is currently active, the blueprint triggers another `skill_sync.push(force=True)` so the deployed body matches the new spec.
4. **Deactivate** — `experiments.deactivate(id)` clears `active` and `activated_at`; the blueprint then redeploys (no conceal) to restore the baseline body.
5. **Delete** — `experiments.delete(id)` removes the row; any `RuleTrigger.experiment_id` values that pointed at it remain for historical rollup.

## Trigger rollup

Two endpoints in `web/blueprints/experiments.py` surface rollups:

- `GET /api/experiments` — `api_experiments` returns every experiment grouped by pattern. Each row carries `trigger_total` and `trigger_fired` counts filtered by `RuleTrigger.experiment_id == <id>` (no time window, no rule scoping) so the list view can show lifetime variant volume.
- `GET /api/experiments/<id>` — `api_experiment_detail` computes a baseline vs. variant comparison via `_rollup`: same rule set (`rules_for_guide(pattern_slug)`), same time window (`checked_at >= experiment.created_at`), differentiated by `experiment_id IS NULL` vs `= <id>`. The response also includes a `per_rule` breakdown (baseline/experiment checks + fired per rule id) and `available_sections` for the edit form. Each rollup carries `{sessions, checks, fired, rate}`; the Vue detail view renders the two side-by-side so a reader can eyeball whether concealing a section changed rule firings.

## UI surface

- `frontend/src/views/ExperimentsView.vue` — list grouped by pattern, with per-experiment trigger totals from `/api/experiments`. Routed at `/experiments` in `frontend/src/router.js`.
- `frontend/src/views/ExperimentDetailView.vue` — edit form, activate/deactivate/delete buttons, baseline-vs-variant rollup including the per-rule breakdown. Routed at `/experiments/:id`.
- `frontend/src/components/PatternExperimentCreator.vue` — embedded under the pattern detail view; lets the operator pick sections from `list_sections` output and `POST /api/experiments` without leaving the pattern page.

## Tests

- `tests/rules/test_experiments.py` — pure conceal filter (H2-only, H3-only, mixed, unknown sections are no-ops, last-section edge cases).
- `tests/rules/test_experiments_crud.py` — CRUD helpers + the one-active-per-pattern invariant, `patterns_with_active`, `get_active`.
- `tests/rules/test_blueprint_experiments.py` — HTTP layer, including that editing an active experiment re-triggers the deploy and that listing groups by pattern with trigger counts.

## Common pitfalls

- The conceal spec must match heading text **exactly** after stripping trailing whitespace — a typo in the section name silently no-ops.
- Deactivating an experiment does **not** purge historical `RuleTrigger` rows; rollups for that experiment keep returning data until those rows age out of the comparison window.
- Only one experiment per pattern can be active at a time; activating a second one auto-deactivates the first inside the same transaction.
- The rule-trigger ingest endpoint does not populate `rule_triggers.experiment_id`. The schema and rollup support the variant/baseline split, but on a fresh install every row will be baseline until a writer that sets the column is wired in.