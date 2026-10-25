## Delivers

R1：`railFolded` 默认 `true`，`?triage=<id>` 到达或变化时展开（D1–D3）；R3：`TaskFilterMenu` 的 feature 字段换成 `FeaturePicker`，当前 `feature_id` 经 `GET /api/features/<id>` 解析出标题，chip 显示 `#F <title>`（未解析退回 `#F`），清空后 Apply 去掉 `feature_id`（D6、D7）；新增 e2e `frontend/tests/f144-triage-filter.spec.js

## Spec 依据

R1, R3, D1, D2, D3, D6, D7

## 验收测试名

- `Triage rail opens folded`
- `A triage deep link unfolds the rail`
- `Feature filter is picked not typed`
- `Arriving with feature_id shows the title in the chip`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
