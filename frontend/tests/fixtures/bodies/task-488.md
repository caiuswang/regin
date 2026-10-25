## Delivers

alembic/versions/0040_*.py`（batch：删除 `agent_task_runs.verify_cmd`、`verify_exit_code`；建 `feature_links`）+ `db/schema.sql` + `lib/orm/models/`（`AgentTaskRun` 删两个字段，新增 `FeatureLink`）。删除 `lib/tasks/verify.py`，以及 runs/arm/store/tail/transcribe/feature_slices/slice_table/slicing/tmux_runs/run_reports/migrate 中的 verify 分支。删除 `settings.tasks.verify_timeout_seconds` 及其 `settings_fields.py` 镜像。前端删除 TaskArmSheet、SliceArmSheet、useTaskArm、useArmPrefs、TaskDetailView、TaskRunHistory 中的 verify 字段。更新 `ARCHITECTURE.md`、`docs/spec-notation.md`、`docs/agent-task-capture-design.md`、`docs/feature-ledger-redesign.md`、`.regin/topics/wiki/spec-slice-to-runs.md`。改写或删除依赖 verify 的测试（`tests/tasks/test_verify_cmd.py` 删除）

## Spec 依据

意图 2, attempt 结算, I1, I2, I5

## 验收测试名

- `test_verify_cmd_is_gone`
- `test_finish_settles_without_verify`
- `test_slice_table_ignores_verify_column`
- `test_migration_0040_drops_verify_adds_links`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
