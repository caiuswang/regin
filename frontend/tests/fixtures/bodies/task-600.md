## Delivers

全量 `.venv/bin/python -m pytest -q -n auto --dist=loadfile` 与 `cd frontend && ./node_modules/.bin/playwright test`；核对 Acceptance 的每个 Scenario 都有同名 E2E 用例

## Spec 依据

Acceptance

## 验收测试名

- `test_full_suite_passes`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
