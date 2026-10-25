## Delivers

pipeline` broadcasts on task/feature/defect writes; shell topic subscription; views re-read on event with an in-flight dirty flag; no polling; never restarts :8321 (live check is S14)

## Spec 依据

R7, I7

## 验收测试名

- `test_pipeline_events_broadcast_on_writes`
- `test_pipeline_views_refresh_on_event`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
