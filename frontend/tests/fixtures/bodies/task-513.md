## Delivers

限流告警：`regin usage measure --session <sid>` 从 stdin 读入 Measure，按“限流告警”表处理。已告警的 `(kind, resets_at, threshold)` 存进新表，唯一约束加插入即占位，保证跨会话只告警一次（J6），不用时间窗。这是一次 schema change：新增一个 alembic revision，同时改 `db/schema.sql` 和 `lib/orm/models/`。新增设置 `settings.agent_messages.rate_limit_alert_at`（默认 `[80, 95]`），并同步加进 SettingsView。告警通过已有的 agent message 加 push 通道发出。压缩上下文：`regin session context --session <sid>` 按“压缩保留的上下文”一节输出，finish 命令与 `lib/prompts/surfaces/tasks.py` 共用同一个渲染函数。新 mod `regin-meter`（`session.measure`）和 `regin-compact`（`session.compact`）按“mod 侧钩子”表实现，用 CC 2.1.295 重新生成 `types/`，带 `*.test.ts`，并登记进 `marketplace.json`（J8）

## Spec 依据

限流告警, 压缩保留的上下文, mod 侧钩子, J6, J7, J8

## 验收测试名

- `test_rate_limit_crossing_raises_one_warning`
- `test_the_same_window_and_threshold_alerts_once_across_sessions`
- `test_a_new_reset_window_alerts_again`
- `test_jumping_past_two_thresholds_alerts_once`
- `test_windows_without_a_reset_time_never_alert`
- `test_meter_skips_measurements_without_rate_limit_changes`
- `test_compaction_keeps_the_feature_task_and_finish_command`
- `test_compaction_without_context_is_untouched`
- `test_subagent_compaction_is_untouched`
- `test_marketplace_lists_every_plugin_dir`

## 收尾动作

合回 `f158/integration`（这是报告完成的前置条件，不是可选步骤）
