## Delivers

新增 `regin uninstall [--dry-run] [--purge-data] [--yes]`。清单就是模型一节表中「是」的行；逐项调用 S1 的 hook 移除函数、按 S4 的记录删 skill 目录、删除 `hook-manager-config.json`，以及指向本 checkout `.venv/bin/regin` 的 `~/.local/bin/regin` 软链。没有归属依据的条目只打印。`--purge-data` 额外删 `settings.data_dir` 和 `db/*.db`。最后打印手工步骤：插件、statusLine、`rm -rf <checkout>

## Spec 依据

R5, 模型 卸载清单, 不变量 4, 不变量 5

## 验收测试名

- `test_uninstall_dry_run_lists_only_owned_artifacts`
- `test_uninstall_removes_owned_artifacts_and_keeps_data`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
