## Delivers

lib/agent_acp/client.resolve_acp_command` 不再写死 `kimi`：argv 由 `provider.acp_argv()` 给出——`KimiProvider.acp_argv()` 返回与现状相同的 `[<which kimi>, "acp"]`，`CodeBuddyProvider.acp_argv()` 返回 `[<which codebuddy>, "--acp"]`；二进制不在 PATH 上时抛 `AcpLaunchRefused`，和 kimi 缺二进制一样拒绝启动。`lib/agent_acp/launcher.py` 与 `lib/agent_acp/registry.py` 按 provider 取 argv。codebuddy 的 `launch_capabilities()` 打开 channel（ACP）与 tmux 两种模式，`channel_launcher()` 复用 ACP launcher，`pane_argv()` 起 tmux `codebuddy`，`skill_invocation()` 为 `/<name>`；workbuddy 仍为空，`GET /api/agent-runs/launch-options` 里 workbuddy 没有任何 launch mode。测试 `tests/agent_acp/test_acp_argv.py`（kimi argv 与改动前逐项相同）、`tests/tasks/test_codebuddy_launch.py`（PATH 上放一个假的 `codebuddy` ACP 脚本，arm provider=codebuddy 后 `start_run` 走 channel launcher、run 拿到 trace_id、hook 进来的 tool span 出现在该 trace 下）

## Spec 依据

Rules 接入面矩阵（启动 task run 行）, Model（acp_argv）, Invariants

## 验收测试名

- `test_acp_command_comes_from_provider`
- `test_codebuddy_task_run_launches_over_acp`
- `test_workbuddy_cannot_be_launched`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
