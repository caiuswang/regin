## Intent

pipeline 的数据层是闭合的:feature ← task ← run,defect → feature,全是单列外键,一到两跳 join。但浏览器上它是三座孤岛 —— `/features`、`/tasks`、`/defects` —— 岛与岛之间唯一的桥是人手抄一个整数 id。

后果不是「少了几个按钮」,是**使用顺序被倒过来**:feature 页看不到自己的 task,于是从 feature 出发的路径第二步就断,操作者被迫从 task 板开始;而网页上报 defect 的唯一控件长在 task 页,于是一个零 task 的 feature —— 模型明确允许、并且能 ship 的形态 —— 在浏览器里根本报不了 bug。

这个 feature 把三条已经存在于数据层的有向边画到界面上,让 feature 页成为一个 feature 全生命周期的单一工作台。

## 三条边

1. **feature → spec**:`features.body` 今天写得进、读不回来 —— `GET /api/features/<id>` 的 payload 没有这个字段,账本页也不渲染它。新建弹窗收的那段规格从浏览器看是只写不读的。这同时是 defect #149。
2. **feature → task**:feature 页不提 task 与 run。过滤已经在(`GET /api/tasks?feature_id=`),创建也已经在(`POST /api/tasks` 的字段白名单含 `feature_id`);缺的只是界面。
3. **feature → defect**:列出与上报的组件已经存在,但只挂在 task 页上。它按 feature 取数,把它挂到 feature 页是搬家,不是重写。

## Non-goals

- **不做 Arm / Run。** 参数面与运行控制留在 task 页。feature 页给出链接即可 —— 把运行机器复制一份是第二个写入者。
- **不做远端 issue 收编。** 它要活的凭据与自己的失败态。
- **不改 defect 的 close 流程。** missing_check 仍然必填,仍然只在 `/defects` 与这块面板上。
- **不动 ship / attribute。** 两者今天就在 feature 页上,且正确。
- **不加删除控件。** feature 只被 abandon。

## Acceptance

验收项即测试名。实现前先让它们红。

### tests/features/test_web_spec.py

- test_the_ledger_carries_the_spec_it_was_declared_with
- test_editing_the_spec_writes_it_back
- test_a_spec_edit_appends_no_event

### frontend/tests/feature-workbench.spec.js

- the feature page shows the spec it was declared with
- the spec can be edited in place
- the feature page lists its tasks and links to each
- a task created here arrives under this feature
- the feature page lists the defects blamed on it
- a defect can be reported from a feature with no task
- a viewer sees no edit, no new task and no report control

## 收尾

做完之后在 `/defects` 上把 **#149** close 掉,missing_check 填
`test_the_ledger_carries_the_spec_it_was_declared_with`。