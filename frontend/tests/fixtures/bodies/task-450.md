## Delivers

新表 `project_stages` 与 `agent_tasks.stage_key`（alembic `0038_project_stages.py` + `db/schema.sql` + `lib/orm/models/` 三处一起改，`stage_key` 回填 `feature:<id>:spec` 与 `feature:<id>:build` 的行）；建表时写入全局默认阶段 spec、build 及其 `kind='stage'` 的 prompt_templates 行。新模块 `lib/features/stages.py`：按 R1 解析阶段列表、copy-on-write、R7 删除/归档。`web/blueprints/` 新增 `GET/POST /api/projects/<id>/stages`、`PATCH/DELETE …/<key>`、`PUT …/order`（`<id>` 可为 `default`，viewer 写操作 403）。`lib/features/steps.py` 与 `POST /api/features/<id>/steps` 改为接受 `stage`（`kind` 兼容），未知阶段 400 `field="stage"`，Task 的 `kind=stage.role`、`stage_key=stage.key`。`lib/tasks/run_prompt.py` 按 R4 注入阶段提示词（变量加 `stage_label`）。测试放在 `tests/projects/test_stages.py

## Spec 依据

B 数据结构, R1, R2, R3, R4, R5, R7, R8

## 验收测试名

- `test_unconfigured_project_reads_default_stages`
- `test_add_stage_copies_defaults_on_write`
- `test_custom_stage_step_injects_stage_prompt`
- `test_legacy_spec_step_matches_without_migration`
- `test_unknown_stage_is_rejected`
- `test_builtin_stage_cannot_be_deleted`
- `test_stage_with_tasks_is_archived`
- `test_viewer_cannot_edit_stages`
- `test_custom_spec_role_stage_writes_same_events`

## 收尾动作

合回 `f148/integration`（这是报告完成的前置条件，不是可选步骤）
