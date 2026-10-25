## Delivers

按“P0 探针”一节回答 Q1–Q5：在 tmux 里跑交互式 claude，探针 mod 放在 `/tmp` 下用 `--plugin-dir` 加载（不进 `~/.claude/dev-mods/`），记下 CC 版本。每行的结论和证据（命令、日志片段）写回本 spec 的 P0 表；Q2 的结论同时写进 F134 映射表第 3 行的“已验证”列（`regin feature spec 134 --file …`）。按 Q3 的结论把“卡片从哪来”一节删到只剩成立的那个分支。Q1 不成立时停下，改写本 spec，以 `--status failed` 结束本片，后面的片不建。本片只写 spec，不改代码

## Spec 依据

P0 探针, 卡片从哪来, J2

## 验收测试名

- `test_probe_findings_are_written_back`

## 收尾动作

合回 `f158/integration`（这是报告完成的前置条件，不是可选步骤）
