## Delivers

R4：新组件 `RefInlineText`（只把 `parseRefs()` 识别的 ref 换成 `RefChip`，其余字符与换行原样，不做 markdown），用于 DefectRow / DefectDetailView / TriageCard / NeedsDecisionPanel；`DefectActionForm` 的 edit 表单改用 `RefTextarea`（`:feature = defect.feature_id`），其他 form 不变；存储格式不变（I4）；新增 e2e `frontend/tests/f144-symptom-refs.spec.js

## Spec 依据

R4, D8, I4

## 验收测试名

- `Editing a symptom takes @ references`
- `Symptom references render on every symptom surface`
- `defect-symptom-verbatim`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
