## Delivers

ARCHITECTURE.md` 增加 provider 启动能力一节（launch_capabilities、通道与 pane 两种 arm、D5 锚定、D6 生命周期）；`README.md` 增加“用 Kimi 跑 task”：前置条件 `kimi login`、在 arm 面板选 kimi、选 ACP 或 tmux、怎么 attach tmux、已知限制；`CLAUDE.md` 与 `AGENTS.md` 只在有需要时改，两者保持逐字一致

## Spec 依据

Intent, 非目标

## 验收测试名

- `test_docs_name_every_launch_capability_field`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
