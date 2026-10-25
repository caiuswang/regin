## Delivers

regin 侧：`regin bridge listen --session <id>`（bridge token 认证，订阅服务端事件，每条发给该 session 的 prompt 输出一行 JSON；连接存续即注册、断开即注销，I2）；`lib/agent_bridge/delivery.deliver()` 在该 trace 有已连接监听者时走 Mod 通道并审计 `channel=mod`，否则 tmux 路径不变（I3）；监听者路由按 session id（I4）。Mod 侧：`regin-plugin/plugins/regin-bridge/`，session.start 用 `$.process.spawn` 起监听进程，每行 → `$.prompt.submit`，进程只起一次；登记进 marketplace.json

## Spec 依据

映射表第 1 行, I2, I3, I4

## 验收测试名

- `test_listener_streams_only_its_own_sessions_prompts`
- `test_deliver_prefers_a_connected_mod_listener`
- `test_deliver_falls_back_to_tmux_without_a_listener`
- `test_listener_disconnect_unregisters_the_channel`
- `test_listener_line_becomes_a_submitted_prompt`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
