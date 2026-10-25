## Delivers

lib/hooks_wiring.py` 提供按 checkout 归属的移除函数，供所有入口共用。`cli/commands/db.py` 删掉私有的 `_strip_hook_manager_entries`，`init --force` 改为调用它。`lib/providers/kimi_hooks.py` 只删命令属于本 checkout 的 managed block，并删掉 `_BEGIN/_END/_BLOCK_RE` 别名。debug hook 的 JSON 移除容错：文件不存在就不写，删除空事件键。JSON 设置一律用 temp 文件加 `os.replace` 原子写。命令构造加 `shlex.quote`，`_VENV_PYTHON_RE` / `interpreter_prefix` 能识别加引号的形式。`regin hooks repair` 发现 foreign 条目时提示 `regin hooks adopt` 并以非 0 退出。测试放在 `tests/hooks/

## Spec 依据

R4, R7 hooks_wiring 命令构造, R7 hooks repair, 不变量 4

## 验收测试名

- `test_init_force_spares_another_checkout_s_hooks`
- `test_kimi_install_spares_another_checkout_s_block`
- `test_hook_commands_survive_a_path_with_spaces`
- `test_repair_points_at_adopt_after_a_checkout_move`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
