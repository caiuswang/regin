## Delivers

第一段每次固定写进切片表的三片（integrate → migrate+doctor → verify）。**其中每一条命令都从 `.regin/commands.json` 读，没有一条写死** —— 写死就只对 regin 成立。

## Spec 依据

R1、W5 的链尾决定、W11

## 验收测试名

- `test_tail_slices_read_every_command_from_the_project_table`
- `test_tail_is_written_by_the_first_skill_not_hardcoded_in_transcription`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。