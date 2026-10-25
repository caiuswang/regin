## Delivers

lib/tasks/arm.py`：缺省 `one_shot` 视为 `true`（单任务、arm-slices、CLI 同一路径）。`lib/agent_sdk/registry.py` 导出占用者快照（`_runs ∪ _reserved` + `run_phase`），由 `agent_task_runs.trace_id` 反查 task。新增 `GET /api/agent-runs/slots`；`POST /api/tasks/<id>/run` 的 409 body 带 `slots`。改写断言旧默认值的测试。测试 `tests/tasks/test_auto_close_default.py`、`tests/agent_sdk/test_run_slots.py

## Spec 依据

Rules B, Model（RunSlots）, Invariants

## 验收测试名

- `test_arm_without_one_shot_defaults_to_auto_close`
- `test_explicit_one_shot_false_is_kept`
- `test_slice_arm_defaults_to_auto_close`
- `test_slots_endpoint_lists_every_holder`
- `test_refused_run_returns_the_slot_holders`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
