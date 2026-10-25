补充defects细节到Task中， 并执行修复

## Defects (feature #139, open at 2026-10-06)

| Defect | Symptom | Reproduced | Root cause | Fix |
|---|---|---|---|---|
| D-252 | Task列表页按照 NO GROUPING 分组时，组件之间字体有重叠 | `/tasks?view=list&group=none`, pixel theme, 1440px: row #371 `WAITS ON #373 (RUNNING)` and running rows' `WATCH`+`STOP` spill left over the `Oct 6` date | `TaskRow.vue` `.task-primary` is a fixed `width: 8.5rem` with `justify-content:flex-end`; Silkscreen keys/labels are wider than 8.5rem, so the content overflows leftwards onto `.task-day` | slot is a minimum (`min-width: var(--pl-task-action-w, 8.5rem)`); pixel sets 11.5rem on the panel and the flat list date head reads the same variable |
| D-249 | Tasks的分组是有缩进的，目前没实现 | `/tasks?view=list` (Group: Next step), pixel: group-head checkbox and row checkbox both at x=304 | `NextStepList.vue` rows render flush with the group head; the mockup indents rows under the head with a 1px rule beneath the chevron | pixel hook on the group's rows (`data-pl-part="group-rows"`) → indent + left rule (flat `TaskGroup` cards already indent via the row gap; unchanged) |
| D-248 | pixel Playwright project re-runs tasks-linear.spec.js concurrently with chromium against the same lin-fixture rows | — | duplicate of D-251, already fixed in b45b4a4f (isolated the pixel project's spec copies) | verify with D-251's check, then close |

Suspect paths: `frontend/src/components/tasks/TaskRow.vue`, `frontend/src/components/tasks/NextStepList.vue`, `frontend/src/pipeline/themes/pixel/tasks.css`.

## Outcome
Fixed in 322b956d, merged to f139/integration (f70d3a0a). D-248/249/252 closed. Pre-existing shared-DB test races logged as D-253, D-254.