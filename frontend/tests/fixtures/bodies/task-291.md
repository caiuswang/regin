## Delivers

regin serve --replica` → `create_app(replica=True)`: skip `_reap_orphaned_agent_runs()`, the workflow watcher and the pending reaper; a `before_request` guard answers non-GET `/api/*` except `POST /api/auth/login` with 403 `{"error":"replica"}`; no behaviour change without the flag

## Spec 依据

D3, I6, I7

## 验收测试名

- `test_replica_skips_boot_side_effects`
- `test_replica_refuses_writes_but_allows_login`
- `test_serve_without_replica_unchanged`

## 收尾动作

合回 `f127/integration`（这是报告完成的前置条件，不是可选步骤）
