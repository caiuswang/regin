把 feature #70 建出来:让 feature 页成为 pipeline 的单一工作台。完整 spec 在 `features.body` 里(`sqlite3 db/regin.db "select body from features where id=70"`),先读它。

三条边,数据层都已存在,缺的只是界面:

1. **spec** —— `GET /api/features/<id>` 的 payload 没有 `body`,账本页也不渲染它。新建弹窗收的规格从浏览器看是只写不读的(= defect #149)。补上读,并在页面上可编辑(`PATCH /api/features/<id>` 已存在)。
2. **task** —— feature 页不提 task/run。列出用 `GET /api/tasks?feature_id=`(过滤已实现),新建用 `POST /api/tasks`(字段白名单已含 `feature_id`)。每行链到 task 页。
3. **defect** —— `frontend/src/components/tasks/TaskDefects.vue` 已经按 feature 取数并能上报,今天只挂在 task 页。把它搬到 feature 页,让零 task 的 feature 也能报 bug。

**验收项即测试名,先让它们红再写实现:**

tests/features/test_web_spec.py
- test_the_ledger_carries_the_spec_it_was_declared_with
- test_editing_the_spec_writes_it_back
- test_a_spec_edit_appends_no_event

frontend/tests/feature-workbench.spec.js
- the feature page shows the spec it was declared with
- the spec can be edited in place
- the feature page lists its tasks and links to each
- a task created here arrives under this feature
- the feature page lists the defects blamed on it
- a defect can be reported from a feature with no task
- a viewer sees no edit, no new task and no report control

**不要做:** Arm/Run 控件(留在 task 页)、远端 issue 收编、改 defect 的 close 流程、删除控件。不加兼容层。

**Gates(贴输出):**
```
.venv/bin/python -m pytest -q -n auto --dist=loadfile tests/features tests/tasks
.venv/bin/python -m radon cc <touched> -n C
cd frontend && ./node_modules/.bin/playwright test tests/feature-workbench.spec.js tests/feature-ledger.spec.js tests/feature-index.spec.js
```
payload 形状变了(ledger 多一个字段),所以按 CLAUDE.md 的规矩跑整套 pytest。vue 改动另跑 `example/rule/vue-complexity` 与 `example/rule/frontend-style-convention` 的 checker(JSON-over-stdin)。

**收尾:**
1. 在 `feat/feature-run-by-click` 上提交(commit message 不要 `Claude-Session:` 行)
2. 浏览器实拍一张 feature 页,确认三块都渲染
3. `regin feature ship 70`
4. `regin defects close 149 --check "test_the_ledger_carries_the_spec_it_was_declared_with"`
5. `regin gate pipeline-ran --session <你的 trace id>`