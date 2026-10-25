## Delivers

检查 S1–S8 都已合回 `f119/integration`，无人落零 commit；然后在集成分支上跑全量 `.venv/bin/python -m pytest -q -n auto --dist=loadfile`、`cd frontend && ./node_modules/.bin/playwright test

## Spec 依据

Work slices, Acceptance, Gates

## 验收测试名

- `test_every_slice_merged_back`
- `test_full_suite_passes`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
