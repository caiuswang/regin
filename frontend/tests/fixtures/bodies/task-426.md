## Delivers

在 `f144/integration` 上跑一次全量 pytest 与四个 f144 e2e spec；本期无 schema 变更，`regin migrate` 应无变化；失败就地修复。不重启主 serve

## Spec 依据

Acceptance

## 验收测试名

- `test_full_suite_passes`
- `f144 e2e specs`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
