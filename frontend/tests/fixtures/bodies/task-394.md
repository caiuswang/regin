## Delivers

/api/agent-runs` POST 接受 `provider`，kimi 走 `channel_launcher()`；`LiveLaunchSheet` 复用 S4 的 provider 选择；Resume 按会话自己的 provider 解析（`provider_for_trace`），不再用 `active_provider`；kimi 会话的 Resume 走 ACP `session/load` 接管；新增 e2e `frontend/tests/live-launch-provider.spec.js

## Spec 依据

D2, Invariant 1, Invariant 2

## 验收测试名

- `test_live_launch_routes_kimi_to_acp_channel`
- `test_resume_uses_session_provider_not_active_provider`
- `test_kimi_resume_loads_session_over_acp`
- `test_live_launch_sheet_offers_provider`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
