## Delivers

TaskRefusal.vue` 渲染 409 的 `slots`：显示 used/limit，按 phase 决策表给出 Close/Open/无操作，Close 成功后移除该行并恢复 Run 按钮。`RunningNow.vue` 头部加 "Run slots used/limit" 芯片（展开同一份列表），新增 "已完成但会话未关闭" 分组。`TaskDetailView.vue` 在 done/failed 任务的会话仍开着时显示横幅。共用组件 `RunSlotList.vue`。e2e `frontend/tests/f167-run-slots.spec.js

## Spec 依据

Rules B（refusal、RunningNow、TaskDetail 行）

## 验收测试名

- `test_refusal_lets_the_user_close_an_idle_holder`
- `test_waiting_input_holder_is_opened_not_closed`
- `test_done_task_with_open_session_shows_close_banner`
- `test_overview_lists_finished_tasks_still_holding_a_slot`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
