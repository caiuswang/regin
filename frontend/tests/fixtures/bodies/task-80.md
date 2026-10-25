## 问题

顶层 task 就是 feature（key `regin:<id>`），但两个页面之间没有任何一条链接：

- `frontend/src/views/TaskDetailView.vue` 上没有 `regin:<id>` 这个 key，也没有到 `/features/regin:<id>` 的入口。操作者在 task 页面看不到 rounds / open / escaped，也就没有理由相信 Feature 这套东西和自己有关。
- `frontend/src/views/FeatureLedgerView.vue` / `FeatureStageLine.vue` 只渲染 key 字符串，不回链 `/tasks/<id>`。

两边都不能在前端按 key 的字面形状拼 URL——`FeatureStageLine.vue` 的注释已经把这条定死了（key 的形状是 provider 的私事）。所以两条链接都得由服务端给出字段：

- task 序列化（`lib/tasks/store.py:_serialize`）补 `feature_key`，取 `parent_id or id`，和 `lib/tasks/runs.py:_feature_key` 同一条规则、同一个 `key_for_task`。
- ledger 载荷（`lib/features/ledger.py::Ledger.to_dict` / `web/blueprints/features.py`）补 provider 给的 `url`（`ReginProvider._ref` 已经产出 `/tasks/<id>`），前端按 url 形状渲染成站内路由——这正是 `_ref` 注释里写好的用法。

## 验收项（写成测试名）

- `test_task_payload_carries_its_feature_key` —— 顶层 task 和子 task 都拿到 `parent_id or id` 的 key
- `test_linked_task_payload_carries_no_native_key` —— 挂了外部 issue 的 task 不给 native key（和 `_feature_key` 的 linked 分支一致）
- `test_feature_ledger_payload_carries_the_ref_url` —— native key 的 ledger 带 `/tasks/<id>`，Linear key 带绝对 URL
- Playwright `a feature task links to its ledger and back` —— task 页点进 ledger，再点回同一个 task

## 不做

不改归属模型，不加表，不动 stage/rounds 的任何算法。