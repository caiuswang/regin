## Delivers

lib/agent_acp/launcher.py` + `lib/tasks/runs.py` + `lib/tasks/run_ending.py` + `lib/agent_sdk/registry.py`：按 D6 的状态机实现 settle；非 `end_turn` 记 failed；one-shot 首 turn 后关闭通道（`_live_runner` 加 ACP 分支）；arm 了 model 就走 ACP `session/set_model`；task images 作为 ACP image content block 发送（agent 未声明 image 能力时 RunRefused 并给出明确原因）；`session/new` 的 cwd 未给时取 `settings.project_root` 的绝对路径；`agent_runs` 行写入 cwd 与 model；测试放在 `tests/agent_acp/test_launcher_lifecycle.py

## Spec 依据

D6, Invariant 2, ACP run 生命周期

## 验收测试名

- `test_acp_non_end_turn_stop_reason_fails_attempt`
- `test_acp_launch_turn_end_does_not_settle_without_finish`
- `test_acp_finish_report_settles_attempt`
- `test_acp_one_shot_closes_channel_after_turn`
- `test_acp_arm_model_applied_via_set_model`
- `test_acp_images_sent_as_content_blocks`
- `test_acp_session_new_cwd_is_absolute`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
