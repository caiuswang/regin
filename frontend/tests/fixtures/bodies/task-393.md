## Delivers

KimiProvider.pane_argv` 与 claude 的 `claude_argv` 一起收进 adapter；`tmux_runs.launch_attempt` 按 provider 取 argv，并先按 D5 锚定 session id（先真机探测，再选路径）；prompt 经 pane delivery 发送，提交用 C-m；pane 守卫按 `pane_commands` 放行；`_binaries_missing` 按 provider 检查；kimi 在 `launch_capabilities` 中声明 tmux；测试放在 `tests/tasks/test_kimi_tmux_launch.py`，真机 e2e 由 `REGIN_E2E_KIMI=1` 门控，覆盖“启动→finish→settle”与“重启 serve 后 liveness 仍为 LIVE”

## Spec 依据

D4, D5, Invariant 1, Invariant 2, Invariant 3

## 验收测试名

- `test_kimi_launch_argv_contains_no_claude_flags`
- `test_task_run_tmux_launch_uses_provider_argv`
- `test_pane_guard_allows_provider_declared_binaries`
- `test_kimi_tmux_session_anchor_is_real_session_id`
- `test_kimi_tmux_prompt_submitted_with_c_m`
- `test_claude_tmux_argv_unchanged`
- `test_real_kimi_tmux_run_settles_via_finish`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
