## Delivers

docs/setup.md` 新增「升级」「卸载」小节，Troubleshooting 不再让人跑 `rebuild`。`docs/setup.md`、`ARCHITECTURE.md` 中「DB 是派生缓存」「from git-tracked files」的说法按 R3 改正。README Quick start 补上 hook 接线、升级和卸载各一行。用 `scripts/gen_cli_reference.py` 重新生成 CLI 参考（含 `uninstall`）

## Spec 依据

R3 文档, R7 docs/setup.md, R7 README

## 验收测试名

- `test_cli_reference_is_current`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
