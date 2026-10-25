## Delivers

New `lib/palette/rows.py`: one batched builder per kind that fills `PaletteRow`. It covers `ref`; title by R3 (session: title → owning task title → first prompt line → `untitled · no prompts`); one-word status by R4 (`awaiting` → needs you, `retrying` → active, task needs you when a run session awaits); owners from `session_features` + `agent_task_runs`; `aside` and `context` (latest prompt, or `asks: …`). Session search items and `/api/session-switcher` rows carry `row`. It makes one query per kind per page, never one per row. Tests go in `tests/palette/test_rows.py

## Spec 依据

Model, R3, R4, Invariants

## 验收测试名

- `test_session_row_names_its_owners`
- `test_untitled_session_takes_task_title`
- `test_untitled_no_prompts`
- `test_retrying_is_active_not_needs_you`
- `test_task_needs_you_when_run_awaits`
- `test_rows_batch_queries`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
