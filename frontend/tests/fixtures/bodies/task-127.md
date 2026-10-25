## Delivers

`.regin/commands.json` 的读取与拒绝（R1）；spec 切片表的列定义与解析器。并从 `master` 建立 `f76/integration`。

## Spec 依据

R1、Model 的 `ProjectCommands` 与 `SliceRow`、不变量 5

## 验收测试名

- `test_commands_missing_key_is_refused`
- `test_commands_null_without_reason_is_refused`
- `test_commands_draft_is_not_written_to_disk`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `master` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。