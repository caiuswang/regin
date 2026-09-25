## Pipeline shell & project rollups

`lib/pipeline/` is the read layer behind the `/pipeline` shell. The shell holds three tabs (tasks, features, defects) plus a Linear-style project list and panel. **Nothing here is stored.** A project is a registered repo id, the same id `features.project_id` and `agent_tasks.project_id` carry, and its status and progress are read off its features' stages.

### Scope (`counts.parse_scope`)

```typescript
type Scope = 'all' | 'none' | RepoId   // anything else → ScopeError (400), never silently widened
```

| Entity | Project axis |
|---|---|
| task | `agent_tasks.project_id` (declared) |
| feature | `features.project_id` (declared) |
| defect | its feature's `project_id` (derived); an unattributed defect falls under `none` so it is visible under some scope |

`counts()` returns all three tab counts in one read, because a count shown only on the active tab carries no information. `projects.py` applies exactly the same scoping, so a project row and the tab strip under the same `?project=` agree.

### Project status (`projects.status_of`, first match wins)

| live features (not abandoned) | any `building` | any `spec` | status |
|---|---|---|---|
| 0 | — | — | empty |
| >0 | yes | — | in_progress |
| >0 | no | yes | planned |
| >0 | no | no | completed |

Progress is `done / live`, where done = `shipped + escaped`. An escaped feature still counts as done-claimed, and its blamed defects are shown beside the bar instead of reducing it.

### API

`GET /api/pipeline/counts?project=`, `GET /api/pipeline/projects` (projects with work first, then by name; "No project" always last), `GET /api/pipeline/projects/<scope>` (rollup, features page, up to 20 open defects and recent tasks; unknown scope → `ProjectNotFound`).

Stage semantics come from [[feature-lifecycle-ledger]]; the Vue views are owned by [[webui-surface]].