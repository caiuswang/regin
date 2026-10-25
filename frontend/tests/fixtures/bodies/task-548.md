## Delivers

新增 `lib/providers/mcp_registration.py`：遍历 `mcp_config_path() != None` 的 provider，合并写入 `mcpServers`——只增删 regin 自己的 `memory` 与 `send-to-user` 两个 key，`send-to-user` 仅当 `capabilities.hooks` 为 true 时注册，`topics` 不注册；非 regin 条目逐字节不变；从不写 `~/.workbuddy/mcp-approvals.json`，不创建 `~/.workbuddy/.mcp.json`。CLI 新增 `regin mcp register [--provider <id>]`（`cli/commands/` 新命令组，在 `cli/regin.py` 注册）；对 workbuddy 输出「需在 WorkBuddy 连接器中心信任 memory / send-to-user」，`regin hooks status` 对 workbuddy 给同样提示。server 的 command/args 与 regin 现有 MCP server 入口一致（`lib/agent_messages/mcp_server.py`、memory MCP server）。测试 `tests/providers/test_mcp_registration.py`（tmp HOME，含已有用户 server 与 approvals 文件的 fixture，approvals 用字节比对）

## Spec 依据

Model（MCP 条目生命周期）, Rules 注册哪些 MCP server, Invariants

## 验收测试名

- `test_mcp_registration_respects_hooks_capability`
- `test_workbuddy_mcp_registration_leaves_trust_to_the_user`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
