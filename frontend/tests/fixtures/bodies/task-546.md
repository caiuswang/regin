## Delivers

hook_manager/runner.py` 解析 `--agent-type` 后，除现有 `data.setdefault('agent_type', flag)` 外再把 flag 写进 `data['provider_id']`；`lib/providers/registry._provider_id_from_tag` 改为在 `agent_type`、`provider_id` 中取第一个已注册的 id，未注册值（如 `"cli"`）跳过。核对 `sessions.agent_type` 的写入路径（span 落库、session upsert）永远写 provider id，绝不写 `"cli"`。测试 `tests/hook_manager/test_codebuddy_attribution.py` 覆盖 Rules「hook 归属到哪个 provider」决策表全部三行，用 `python -P -m hook_manager PostToolUse --agent-type workbuddy` 跑一条带 `agent_type:"cli"`、`client:"WorkBuddy"`、`model:"fast-model"` 的 payload，`settings.active_provider=claude

## Spec 依据

Rules hook 归属, Invariants

## 验收测试名

- `test_hook_with_agent_type_cli_is_attributed_by_the_flag`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
