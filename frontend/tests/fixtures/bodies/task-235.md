## Delivers

running {total, rows}` on /api/pipeline/overview; "Running now" block heading the Run next card, desktop only

## Spec 依据

Shape (Overview.running, RunningRow), Rules (Overview ordering)

## 验收测试名

- `test_overview_running_now_*`
- `test_overview_panels_and_empty_states`

## 收尾动作

合回 `f110/integration`（这是报告完成的前置条件，不是可选步骤）
