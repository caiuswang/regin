## Delivers

lib/agent_sdk/worktree.py::check_target`（复用 `repo_root` / `_branch_exists` / `_ref_exists`），由 `parse_arm` 之后的 arm 路径（单个与 arm-slices）调用；`WorktreeRefused` 带 `field`。`runs.start_run` 及 tmux、channel 路径把 `_launch_kwargs` 的异常交给 `_refuse_launch`。`cli/commands/tasks.py` 新增 `arm`（R3）。web 层 400 带 `field`。测试放在 `tests/tasks/test_arm_git_check.py

## Spec 依据

R2, R3, I3

## 验收测试名

- `test_arm_refuses_unknown_base_ref`
- `test_arm_accepts_existing_branch_without_base`
- `test_arm_refuses_missing_cwd`
- `test_arm_slices_refuses_bad_base_per_row`
- `test_launch_refusal_leaves_no_starting_row`
- `test_cli_task_arm`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
