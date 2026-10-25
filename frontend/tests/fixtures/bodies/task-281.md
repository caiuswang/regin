## Delivers

D1/D2 activity SQL + `Z` emission; the false comment rewritten; `utils/time.js` with `localDay`/`localStamp`/`parseUtc`; shortDate/taskDay/monthDay routed through it; task detail, run history and comment stamps localized

## Spec 依据

P1, D1, D2, D5, R1, I1

## 验收测试名

- `test_feature_activity_normalizes_local_session_stamp`
- `test_feature_activity_sort_across_clocks`
- `test_feature_activity_counts_events_defects_tasks`
- `test_feature_activity_emits_zulu`
- `test_local_day_helpers_agree`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
