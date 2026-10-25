## Delivers

CLI：`regin messages list [--unread] [--json]` 和 `regin messages read <id>... / --all`，都调 `lib/agent_messages/store` 已有的 `list_inbox`、`mark_read`、`mark_all_read`。Mod：`regin-band` 注册 `/regin-inbox` 命令，打开一个 Pane 列出未读消息（标题、类型、时间），带 “Mark all read” 按钮；横条加一个打开 Pane 的按钮；按下按钮后刷新 `$.state` 里的计数

## Spec 依据

第二轮 #3, I1

## 验收测试名

- `test_messages_list_prints_unread_as_json`
- `test_messages_read_marks_the_given_ids_read`
- `test_regin_inbox_command_opens_the_inbox_pane`
- `test_mark_all_read_clears_the_pane`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
