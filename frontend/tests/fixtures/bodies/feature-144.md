# Feature #144 — Pipeline 操作逻辑优化（五期）

> 原始需求：
> 1. 目前 Feature 页面，Triage 大多数场景下都是空白的，所以改为默认折叠。
> 2. Feature 详情页进去以后，点击 Task 看到一堆 Task，也不知道它们的依赖关系，最好能预览（Graph）或者跳转到 Task 页面，带着 Feature 条件，方便筛选。
> 3. Task 页面的 Feature 筛选都是靠人手工输入，不方便，改为列出已有的。
> 4. Defects 写 symptom 的时候，目前不支持 @ 操作，支持一下。
> 5. Overview 界面和 Task 的 'Next Step' 界面目前不会显示 Done 的 Task，但很多时候需要看已完成的（进度、是否完成正确），所以加一个 Done 组别。
> 6. 执行过程中发现的其他缺陷，也可以修复。
>
> Spec written 2026-10-08 against `master` @ `30ca7913`.

## Intent

五个独立的小改动，各自让一个已有界面少一步手工操作：triage 栏不再默认占位；从 feature 能一跳看到它的 task 及依赖；按 feature 筛 task 用选的不用敲；symptom 和其它 body 一样能 `@` 引用；已完成的 task 在 Next step 和 Overview 里看得到。

## Root cause (as found in code)

| # | Surface | Today | Gap |
|---|---|---|---|
| R1 | `/features` (`FeaturesView.vue`) | `railFolded = ref(false)` — 每次进入 `TriageRail` 都展开占 21rem | 默认值错；折叠态的 52px strip 已经显示 queue 数（`triage-fold-count`），折叠不丢信息。`?triage=<id>` 到达时 `focusRequested` 在 folded 下直接 return |
| R2 | `/features/:id` Tasks 卡片 (`FeatureTasks.vue`) | 按创建顺序平铺，每行只有状态/kind/标题/fixes/runs | 行数据里已有 `blocked_by` / `waits_on`（`lib/tasks/rows.py::dependencies`，`GET /api/tasks` 已 enrich），卡片没画；卡片没有去 `/tasks?feature_id=` 的入口，Tasks 页 Graph（`view=graph`，`GRAPH_FILTERS` 含 `feature_id`）已支持按 feature 画 lane |
| R3 | `/tasks` Filter 菜单 (`TaskFilterMenu.vue:154`) | `<Input type="number" placeholder="73">` | 已有 `FeaturePicker.vue`（`GET /api/features?q=`，聚焦即列出最近活跃的）在 6 处使用，Filter 菜单没用；激活后 chip 只显示 `#73`（`utils/taskChips.js:42`） |
| R4 | Defect symptom | `NewDefectDialog` 已用 `RefTextarea`；但 **Edit symptom**（`DefectActionForm.vue` → 纯 `Textarea`）不支持 `@`；且所有展示 symptom 的地方都是 `{{ defect.symptom }}` 纯文本，写进去的 `[label](kind:key)` 原样显示成 markdown 源码 | 编辑入口缺 `RefTextarea`；展示端缺 ref 渲染 |
| R5a | `/tasks` Next step (`lib/tasks/next_step.py`) | 五组 ready/running/blocked/failed/ideas；done 只计数为 `done_hidden`，标题行显示 "N open · M done hidden" | 没有 done 组 |
| R5b | `/pipeline` Overview (`lib/pipeline/overview.py`, `RunNextPanel.vue`) | 只有 Running now + Run next + Needs decision | 没有最近完成的 task |

## Shape

```ts
// R1 — FeaturesView
railFolded: Ref<boolean> = ref(true)          // 不持久化；KeepAlive 内切 tab 保留本次选择
// route.query.triage 非空 ⇒ railFolded = false（到达时 & 变化时）

// R2 — FeatureTasks 行 & 头部
type FeatureTaskRow = Task & { blocked_by: number[]; waits_on: { id: number; status: string } | null }
// 头部链接（只读导航，不带 run 机器）
tasksListTo  = { path: '/tasks', query: { feature_id: id, view: 'list' } }
tasksGraphTo = { path: '/tasks', query: { feature_id: id, view: 'graph', open: '0' } }  // open=0: 已完成的链也画

// R3 — TaskFilterMenu
// feature_id 字段换成 <FeaturePicker v-model="draft.feature_id" :current="featureRow" testid="task-filter-feature" />
// featureRow: { id, title } | null —— 由 GET /api/features/<id> 解析当前 state.feature_id 得到
// chip: { field: 'Feature', value: `#${id} ${title}` }（标题未解析出来时退回 `#${id}`）

// R4 — 渲染
// <RefInlineText :text="symptom" />：只把 parseRefs() 识别的 [label](kind:key) 替换成 RefChip，
// 其余字符原样（保留换行，不做 markdown）。用在 DefectRow / DefectDetailView / TriageCard / NeedsDecisionPanel
// DefectActionForm: form === 'edit' 时用 RefTextarea（:feature = defect.feature_id），其余 form 不变

// R5a — next_step
GROUP_DONE = 'done'
GROUP_KEYS = (ready, running, blocked, failed, ideas, done)
membership(done) = status == 'done'
group_order(done) = coalesce(latest_run.ended_at, updated_at) DESC, id DESC
DONE_PREVIEW = 5            // 和 ideas 一样折叠预览，Show more 走 only=done 分页
type GroupsBody = { groups: Group[]; open: number /* 仍只算前五组 */ }   // done_hidden 删除
// NEXT_STEP_GROUPS (useTaskQuery.js) += 'done'；?group=done 是合法地址

// R5b — overview
type RecentlyDone = { week: number /* next_step.done_since(DONE_WEEK) 计数 */; rows: RunNextRowBase[] /* 与 week 同一窗口（本周完成）内的 ≤ 5 条，排序同 done 组 */ }
GET /api/pipeline/overview → { ..., recently_done: RecentlyDone }   // 遵守 project scope
// RunNextPanel 底部 "Done recently · <week> this week" 块；"All done →" 链接 /tasks?view=list&group=done（带 scope）
```

## Rules

| # | Condition | Outcome |
|---|---|---|
| D1 | 进入 `/features`，无 `?triage` | rail 折叠；strip 显示 queue 数 |
| D2 | 进入 `/features?triage=<id>` | rail 展开，cursor+focus 落在该卡（现有 `focusRequested` 行为） |
| D3 | 用户点展开后切到别的 Pipeline tab 再回来 | 保持展开（KeepAlive）；整页刷新后回到折叠 |
| D4 | Feature task 行 `waits_on != null` | 行内显示 "waits on #N"（链接到 `/tasks/N`），N 用 `waits_on.id`；`blocked_by.length > 1` 时追加 "+k" |
| D5 | Feature task 行 `waits_on == null` | 不显示依赖提示 |
| D6 | Filter 菜单 Feature 字段聚焦、未输入 | 列出 `/api/features` 前 20 条（activity desc），可搜索 |
| D7 | Filter 菜单里清空 feature chip 并 Apply | URL 去掉 `feature_id` |
| D8 | symptom 文本含合法 ref | 渲染成 RefChip，可点击跳转；非法/代码内的 `[x](y)` 原样显示 |
| D9 | Next step 组别 | 顺序 ready, running, blocked, failed, ideas, done；done 默认折叠预览 5 条 |
| D10 | 有 `status` 筛选时的 grouped 视图 | 不变：grouped 视图本来就拥有 status 轴 |
| D11 | Overview `recently_done.rows` 为空（scope 内本周无完成的 task；更早完成的不算） | 不画 Done 块 |

## Invariants

- I1 `open == ready.n + running.n + blocked.n + failed.n + ideas.n`；done 不计入 `open`。
- I2 六个组划分 `archived` 以外的全部 task（每个非 archived task 恰好落在一个组）。
- I3 Feature 详情页 Tasks 卡片仍然不带 Arm / Run（沿用 `FeatureTasks.vue` 的既有决定）；"预览依赖"靠行内 waits-on 提示 + 跳到 Tasks 页 Graph，不在卡片里嵌 `TaskLaneGraph`。
- I4 symptom 存储格式不变（纯字符串，ref 以 `[label](kind:key)` 写入）；不改 dedup 规则。

## Non-goals

- 不把 defect symptom 加进 `lib/refs/backlinks.py` 的 "Referenced by" 来源（如需要，另开 follow-up）。
- 不持久化 triage rail 的折叠选择。
- 不改 Board 视图（它已有 Done 列）。
- 第 6 条：执行中发现的其它缺陷，每一个先 `regin defects open "<what>" --feature 144` 记录，再修；修不了的留在 defect 里，不扩大本 spec 的范围。

## Acceptance

```gherkin
Scenario: Triage rail opens folded
  Given the triage queue has 2 defects
  When the operator opens /features at ≥ md width
  Then [data-testid=triage-fold-count] reads "2"
  And the rail column is 52px wide

Scenario: A triage deep link unfolds the rail
  When the operator opens /features?triage=<defect id>
  Then the rail is unfolded and [data-testid=triage-card-<id>] has keyboard focus

Scenario: Feature task rows show what they wait on
  Given feature F with tasks A and B, B depends_on A, A armed
  When the operator opens /features/F
  Then B's row in [data-testid=feature-tasks-list] shows "waits on #A" linking to /tasks/A
  And A's row shows no waits-on hint

Scenario: Feature tasks jump to the Tasks page scoped to the feature
  Given /features/F
  When they click the Tasks card's "Graph" link
  Then the URL is /tasks?feature_id=F&view=graph&open=0
  And the graph draws F's lane including its done tasks
  When they go back and click "Open in Tasks"
  Then the URL is /tasks?feature_id=F&view=list and only F's tasks are listed

Scenario: Feature filter is picked, not typed
  Given /tasks with the Filter menu open
  When they focus the Feature field without typing
  Then a list of existing features appears
  When they pick "#F <title>" and apply
  Then the URL has feature_id=F
  And the active chip reads "Feature: #F <title>"

Scenario: Arriving with feature_id shows the title in the chip
  When the operator opens /tasks?feature_id=F
  Then the active chip reads "Feature: #F <title>"

Scenario: Editing a symptom takes @ references
  Given a defect D
  When they choose "Edit symptom", type "@" and pick feature F from the menu, then Save
  Then PATCH /api/defects/D carried a symptom containing "](feature:F)"
  And D's row renders that reference as a chip linking to /features/F, not as "[...](feature:F)"

Scenario: Symptom references render on every symptom surface
  Given a defect whose symptom contains "[x](task:T)"
  Then DefectRow, /defects/D, the TriageCard and the Overview Needs-decision row each show a chip for task T

Scenario: Next step lists done tasks last
  Given 7 done tasks and 1 armed task
  When the operator opens /tasks (next step grouping)
  Then the last group is "Done" with count 7, collapsed to 5 rows, newest-finished first
  And the header reads "1 open · 7 done"
  And GET /api/tasks?group=next_step response has no "done_hidden" key and its "open" is 1

Scenario: Group address for done
  When the operator opens /tasks?group=done
  Then the next-step list opens scrolled to the Done group

Scenario: Overview shows recently done tasks
  Given 3 tasks in project P finished within the last week
  When the operator opens the Overview scoped to P
  Then the Run next panel shows a "Done recently" block with those 3 rows, newest first
  And its "All done →" link goes to /tasks?view=list&group=done&project=P

Scenario: Overview with nothing done hides the block
  Given no done tasks in scope
  Then the Overview has no "Done recently" block
```

Backend checks (pytest): `tests/tasks/test_next_step.py` asserts I1, I2, done ordering and the removed `done_hidden`; an overview test asserts `recently_done` honours project scope and the 5-row cap.

