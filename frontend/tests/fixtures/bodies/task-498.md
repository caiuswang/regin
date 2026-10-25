## Delivers

删除 `lib/grader/store.py` 建 `session_grades`、`hook_manager/handlers/bridge_registry.py` 建 `bridge_panes`、`lib/topics/wiki_backfill.py` 补列的运行时 DDL，表和列以基线与迁移链为准（若链上缺失，回报给 S2 的 owner，不在本片补）。删除 `lib/memory/engine.py` 中 `topic_negatives → topic_exemplars` 的改名和 6 月的补列 ALTER，以及 `lib/memory/models.py` 的 `TopicNegative` 别名，测试改用 `TopicExemplar`。`lib/mysql_db.py` 改读 `settings.database_url`，接受 `mysql://` 和 `mysql+pymysql://`，删掉 `db_*` 键。`cli/commands/users.py` 的 `users init-db` 只建 auth 引擎上的表。删除 `lib/hook_plugin.py` 的 `REGIN_RULE_TRACE_URL` 回退和 `lib/patterns/pattern_deployments.py` 的 `backfill_null_providers

## Spec 依据

R7 启动期 DDL, R7 memory engine, R7 mysql_db, R7 users init-db, R7 死代码

## 验收测试名

- `test_upgraded_schema_matches_fresh_schema`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
