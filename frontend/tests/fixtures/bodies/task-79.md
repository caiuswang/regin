## 问题

对一个已经做完的 task 发现了更多 bug，想提 defect 关联回它——今天只有一条路，而且是 CLI：

```
regin defects open "<症状>" --feature regin:<task id> --session <trace id>
```

两个缺口：

1. **Web 上完全没有开 defect 的入口**。`web/blueprints/features.py` 只有只读的 ledger 加 attribute/unattribute，没有 POST。人在 Task 页面上看着那个 bug，却要切到终端去敲。
2. **报完之后没法一键变成可跑的活**。`regin defects fix-item <id> --create` 本该在 feature 下开一个子 issue 并挂上指针，但对 native feature 直接抛错——`lib/tasks/integrations/regin.py` 没实现 `create_issue`，落到 `lib/tasks/integrations/base.py:145` 的默认实现：`"regin cannot create issues from regin — attach an existing one with --issue"`。只有 Linear 实现了它。

所以「不必新建 Task」这个答案今天只对了一半：记录 defect 确实不用，但要修它还是得手工建子 task、再手工 `--issue` 挂上。

## 决策

补齐这两处，让「在 Task 页面报一个 defect → 它变成挂在这个 task 下的子 task」成为闭环。

前提不变：defect 归属的是 **feature key** 不是 task，native task 的 key 是 `regin:<id>`（`lib/tasks/integrations/regin.py:110`），而子 task 的 run 自动共享父 task 的 key（`lib/tasks/runs.py:311` 取 `parent_id or id`）。所以子 task 跑出来的 session、成本、rounds 全部照旧汇总到原 feature。**这次不动归属模型的任何一部分。**

## 行为

### 1. regin provider 实现 `create_issue`

`lib/tasks/integrations/regin.py`，签名照 `base.py:145`：`create_issue(*, parent, title, body) -> ExternalRef`。

- `task_id_for(parent.key)` 解析父 task id；解析不出或该行不存在 → `IntegrationError(retryable=False)`
- `store.capture_task(title=..., body=..., parent_id=<父 id>, source=...)`，用一个专属的 `source` 值标明这行是 defect 开出来的
- 返回 `self._ref(new_task_id)`

只写 base 允许的三个字段：parent、title、body。**不写 label / assignee / priority**——base 的 docstring 明说这些有人类作者，猜它们是 feature pipeline 禁止的镜像行为。

`capture_task` 内部已经调 `grain.refuse_bad_parentage`，父子 grain 规则自动生效，不要在这里再写一遍。

### 2. 开 defect 的 HTTP 入口

新增 `POST /api/defects`，body: `symptom`（必填）、`feature_key`、`suspect_paths`。落到 `lib.defects.store.record_reporting`，认证沿用 tasks blueprint 上已有的装饰器。

返回必须区分 **opened** 和 **deduped**——`store` 已经用 `OPENED` / `DEDUPED` 把这件事显式化了，理由写在 `lib/defects/store.py:36`：调用方通常是 agent，把静默 no-op 读成开单成功是它以为自己报了但没报的原因。HTTP 层不能把这个区分吃掉。

`session_id` 由服务端从请求上下文取，不接受客户端传——人在 Web 上报的 defect 不属于任何 trace，留空即可（unattributed 是一等状态，但这里 `feature_key` 有值，所以不是 unattributed）。

### 3. Task 页面的入口

在 task 详情页加「报 defect」控件，与 `TaskShipControl.vue` 同级。

**只对 top-level task 出现**（`parent_id` 为空）。子 task 是 run 的粒度不是 feature 的粒度，在它上面报 defect 会让人以为归属的是子 task。feature_key 由 `regin:<id>` 现场拼出，不让人手填。

提交后展示 defect id 与 opened/deduped 结果。

### 明确排除

- **不在 `defects open` 时自动建子 task**。`cli/commands/defects.py:279` 已经写下理由：多数 defect 在发现它的那次 session 里就修掉了，自动建会让看板堆满一读就已经 Done 的条目。建子 task 始终是 `fix-item --create` 这个单独的动作。
- **不做 defect 列表 / 看板 UI**。ledger 已经显示 open/total 计数，列表另议。
- **不碰 Linear 那一侧**，它的 `create_issue` 已经能用。

## 验收项（每条一个测试）

1. `provider.create_issue(parent=regin:<N>, title=..., body=...)` 建出一个 `parent_id == N` 的 task，返回的 ref key 是 `regin:<新 id>`
2. parent 指向不存在的 task → `IntegrationError`，`retryable is False`
3. `regin defects fix-item <id> --create`，defect 的 feature 是 `regin:<N>` → 建出 N 的子 task，并把 `fix_provider` / `fix_external_key` 写成该子 task
4. 同一命令在 unattributed defect 上 → 仍然拒绝，提示先 `attribute`（现有行为不回归）
5. `POST /api/defects` 开单成功，返回 defect id 与 opened
6. 同一 `(feature_key, symptom)` 再 POST 一次 → 不产生第二行，返回 deduped
7. 未认证的 `POST /api/defects` → 拒绝
8. 「报 defect」控件在 top-level task 上可见，在有 `parent_id` 的 task 上不可见