# F148 Pipeline 操作逻辑优化（六期）

## 意图

完整的工作闭环是 **Feature → Task → Defect**，但 Pipeline 的 Tab、Overview 布局和右上角创建按钮仍以 Task 为先。本期做两件事：

1. **A — 以 Feature 为先**：Tab 顺序、Overview 布局、Overview 的默认创建动作都按 Feature → Task → Defect 排列。
2. **B — 每个项目自定义步骤阶段（step stage），每个阶段带自己的提示词**：现在每个 Feature 只有写死的两个步骤（Spec、Build，见 `lib/features/steps.py::VERB_KINDS`），而且只有 Spec 有一段提示词（`task-run-spec` surface）。改为每个项目维护一张有序的阶段表，默认是 Spec、Build，项目可以新增阶段，每个阶段的提示词都能编辑。另外提供一个**项目管理页**作为这些设置的入口。

## 两个容易混淆的「阶段」（必须分开，不得合并）

| 轴 | 定义位置 | 取值 | 本期是否可配置 |
|---|---|---|---|
| **生命周期阶段（lifecycle stage）**：Feature 当前处在哪 | `lib/features/events.py::STAGES`，由 `feature_events` 账本推导 | spec / building / shipped / escaped / abandoned | **否**。rounds-to-done、escape、Ship 的拒绝规则、`next_step` 都依赖这五个值 |
| **步骤阶段（step stage）**：可以为 Feature 开哪一步 Task | 本期新增 `project_stages` | 默认 Spec、Build，项目可增删 | **是** |

需求原文里的「Feature 阶段改为不固定」只对应第二个轴。第一个轴不改。

## A — 以 Feature 为先

### 决策表

| 位置 | 现状 | 本期 |
|---|---|---|
| `PipelineView.vue` 的 `TABS` | Overview, Tasks, Features | **Overview, Features, Tasks**（`/defects` 仍点亮 Features） |
| `lib/pipeline/overview.py::_loop` 的 stages 顺序 | Tasks, Features, Defects | **Features, Tasks, Defects**；`fix_tasks` 回边不变 |
| `KeepAlive` 的 `CACHED_TABS` | `['OverviewView','TasksView','FeaturesView']` | 顺序随 TABS 调整（仅为一致，不影响行为） |
| Overview 第一屏内容 | `RunNextPanel`（Task）在左，`NeedsDecisionPanel` 在右 | 在 Task 面板之前，先放一个 **Features 面板**（见下） |
| Overview 右上角创建按钮 | `<PipelineCreateButton kind="task">`，显示「New task」，`c` 打开 Task | **`kind="feature"`**，显示「New feature」，`c` 打开 Feature 对话框；▾ 下拉仍然提供 Task · Feature · Defect 三种 |
| Tasks Tab、Features Tab 的创建按钮 | task / feature | 不变 |

### Overview 的 Features 面板

```ts
type OverviewBody = {
  loop: { stages: [FeaturesStage, TasksStage, DefectsStage]; fix_tasks: number }
  features_in_flight: {           // 新增，所有作用域下都返回
    total: number                 // 处于 spec + building 的 feature 数，与 loop 中 Features 的 Spec+Building 两部分之和相等
    rows: { id: number; title: string; stage: 'spec'|'building'; project_id: number|null;
            last_activity_at: string }[]   // 按 last_activity 倒序，上限 5
  }
  // run_next / running / recently_done / needs_decision / projects / project_head / project_features：不变
}
```

- 布局：`features_in_flight` 和 `NeedsDecisionPanel` 组成第一行网格；`RunNextPanel` 放到它们**下方**，占满整行。小于 1024px 时顺序是 Features、Needs decision、Run next。
- 作用域为单个项目时，`ProjectScopePanel` 已经列出该项目的 features。此时把 `features_in_flight` 放在它的位置，避免同一份列表画两遍：`ProjectScopePanel` 只保留项目信息头，不再显示 feature 列表。
- 「View all」链接到 `/features?stage=…`，并带上当前作用域。

## B — 项目步骤阶段与阶段提示词

### 数据结构

```ts
type ProjectStage = {
  id: number
  project_id: number | null      // null = 全局默认阶段（没有项目的 feature 用它，项目首次自定义时也从它复制）
  key: string                    // 项目内唯一，格式 /^[a-z][a-z0-9-]{0,31}$/，默认阶段为 'spec'、'build'
  label: string                  // 'Spec'、'Build'、'Design review'…
  role: 'spec' | 'build'         // 决定这一步在账本与 run 中的语义（见规则 R3）
  position: number               // 在阶段列表中的顺序
  prompt_slug: string | null     // prompt_templates.slug；null = 这一步没有阶段提示词
  builtin: boolean               // 默认的 spec/build 为 true：可改名、可改提示词，不可删除，不可改 role
  archived_at: string | null     // 软删除；已有 Task 的阶段只能归档，不能物理删除
}
```

改表需要同时改三处：新增 `alembic/versions/00NN_project_stages.py`，`db/schema.sql` 加同样的表，`lib/orm/models/` 加 SQLModel 模型。`agent_tasks` 新增可空列 `stage_key TEXT`。

阶段提示词存为 `prompt_templates` 的行，`kind='stage'`，slug 为 `stage-<project_id|global>-<key>`，编辑器沿用 Prompt Templates 现有的编辑器（`PromptBody` / 变量面板）。

### 规则

| # | 规则 |
|---|---|
| R1 | **阶段列表的解析**：项目在 `project_stages` 中有自己的行时，使用项目行；没有时，使用全局默认行。首次写入（新增、改名、改提示词、排序）时，先把默认行复制成该项目的行（copy-on-write），再执行修改。没有项目的 feature 一律使用全局默认行。 |
| R2 | **步骤的身份**：`external_id = feature:<id>:<stage.key>`。默认阶段的 key 就是 `spec` / `build`，所以已有的步骤 Task 不需要迁移，依然能匹配到原来那一行。同一 feature 的同一阶段只对应一行 Task，再次运行就是该行的又一次 run，不会新建行（与现状一致）。 |
| R3 | **语义只取决于 role，不取决于 key**：步骤 Task 的 `kind` 等于 `stage.role`（值仍在现有的 `TASK_KINDS` 中），`stage_key` 存在新列里。所以 `_ON_RUN_START`、rounds、lane 的归属都不变。kind 和阶段是两个独立的轴，不能把 key 塞进 kind。 |
| R4 | **提示词注入**：`run_prompt` 中，步骤 Task 的阶段提示词按以下优先级取：①该阶段的 `prompt_slug` 行 → ②`role=spec` 时退回现有的 `task-run-spec` surface → ③为空。渲染时可用的变量与 `task-run-spec` 相同（`feature_named`、`spec_cmd`），另加 `stage_label`。渲染出错时退回内建默认值，与 `render_surface` 的现有保证一致。 |
| R5 | **创建步骤**：`POST /api/features/<id>/steps` 的请求体改为接受 `stage`（阶段 key）；旧字段 `kind=spec|build` 等价于 `stage=spec|build`。key 不属于该 feature 项目的有效（未归档）阶段时，返回 400，`field="stage"`。 |
| R6 | **Feature 页面**：`FeatureTasks.vue` 不再写死 Spec / Build 两个按钮，改为按 `position` 为每个未归档阶段渲染一个按钮；Feature 已有切片（slices）时不渲染 `role=build` 的阶段按钮——切片本身就是它的 build（沿用 F119 对 Build 按钮的规则）。`FeatureStepDialog` 显示该阶段的 label，以及阶段提示词的预览（只读）。 |
| R7 | **删除与归档**：builtin 阶段不能删除，也不能归档（账本至少需要一个 build 入口）。自定义阶段没有 Task 时物理删除，有 Task 时只能归档；归档后不再出现在按钮里，已有的 Task 照常显示。 |
| R8 | **权限**：viewer 角色只能查看阶段和提示词；增删改返回 403，前端隐藏编辑控件。 |

### 项目管理页

| 项 | 内容 |
|---|---|
| 路由 | `/projects/:id`，挂在 Pipeline shell 之下，显示 Tab 条但不点亮任何 Tab，不进入 KeepAlive 缓存。全局默认阶段的管理页是 `/projects/default`。**套用 Pipeline 主题**（与各 Tab、`/defects` 相同，pixel 下 `html[data-pl-theme="pixel"]`）；行详情页（`/features/:id` 等）仍不套用 |
| 入口 | `ProjectPicker` 下拉中的「Project settings…」（作用域为单个项目时出现）；`ProjectScopePanel` 头部带文字的「Project settings」按钮（不是只有图标） |
| 本期包含的分区 | **Stages**：有序列表，可新增、改名、调整顺序、设置 role（仅自定义阶段）、归档或删除；每行展开后是该阶段的提示词编辑器。**基本信息**：项目名和路径（只读），链接到 `/repos/:name` |
| API | `GET/POST /api/projects/<id>/stages`，`PATCH/DELETE /api/projects/<id>/stages/<key>`，`PUT /api/projects/<id>/stages/order`；`<id>` 可以是 `default` |

#### 页面布局（沿用 Overview 面板的既有 UI）

页面不发明新样式，只用 Overview 已有的 `.pl-panel` 结构和 `data-pl-part` 钩子，所以 classic 与 pixel 都是「同一份标记，两套皮肤」。pixel 皮肤只在 `pipeline/themes/pixel/projects.css` 里补本页独有的部分（阶段行、对话框外的字段标签、提示词预览框），范围限定在 `[data-pl-part="settings"]` 之下。

| 区块 | 结构（钩子） | 内容 |
|---|---|---|
| 项目面板 `project-info` | `panel` › `panel-head` | `← Overview`（`panel-link[data-pl-back]`）、项目名（`panel-title`）、「project settings」/「default stages」（`panel-meta`）、右侧「Open repo →」（`project-info-repo`）；下面一行只读 Path |
| 阶段面板 `project-stages` | `panel` › `panel-head` | 「Stages」+「N steps a feature can open」；项目尚未有自己的阶段时，带虚线标签「inherits the defaults」 |
| 阶段行 | `row`，行内是固定网格 | 拖动柄 · 序号（`row-id`）· 名称（可直接编辑的输入框）· key（仅当 key ≠ role，即自定义阶段）+ role（自定义阶段为下拉）+ built-in/archived 虚线标签 · 右侧操作：↑ ↓ / Remove / Prompt▾（无提示词时显示「No prompt」）。<640px 时标签与操作折到名称下方 |
| 展开的行 | `row[data-expanded]` | 变量按钮、提示词编辑框、预览（`preview`）、Revert / Save prompt |
| 归档 | `group-label` | 「Archived · tasks kept, button gone」及只读行 |
| 新增 | `panel-foot` | 「Add a stage」：Label、Key、Role、Add stage |

classic（浅色）：

![](image:1)

pixel（浅色，展开一行的提示词）：

![](image:2)

pixel（深色）与手机宽度：

![](image:3)

![](image:4)

API 形状（S1 已实现，`web/blueprints/project_stages.py`）：

```ts
type StageView = ProjectStage & { prompt_body: string | null }   // 阶段提示词正文，供编辑器与只读预览
GET    /api/projects/<id>/stages          → { project_id: number|null; inherited: boolean; stages: StageView[] }  // 含已归档行（archived_at 非空），按钮自行过滤
POST   /api/projects/<id>/stages          { key; label; role; prompt? }   → 201 { stage }
PATCH  /api/projects/<id>/stages/<key>    { label?; role?; prompt? }      → 200 { stage }   // prompt 为正文；写到该项目自己的 stage-<id>-<key> 行
DELETE /api/projects/<id>/stages/<key>    → 200 { deleted; archived; stage }
PUT    /api/projects/<id>/stages/order    { keys: string[] }  // 恰好是全部未归档 key → 200（同 GET）
// 错误：{ error; field }，400 校验 / 404 未知项目或阶段 / 409 key 重复或 builtin 删除；viewer 写操作 403
```

- 复制（copy-on-write）与这次修改在同一事务内提交：被拒绝的写不会留下项目自己的行。
- 阶段提示词为空时视为「没有阶段提示词」，按 R4 往下退。全局默认 spec 阶段的提示词是 `{{include:task-run-spec}}`，所以两处都能编辑。

## 不做的事

- 生命周期阶段（spec/building/shipped/escaped/abandoned）及其账本、verb、Ship 规则保持不变。
- 不做按项目覆盖其他 prompt surface（finish、references、tracker…），只覆盖阶段提示词。
- 不做阶段之间的自动推进或依赖（例如 Spec 完成后自动开 Build），阶段只是「可以开哪几步」的列表。
- 不迁移已有 Task，`stage_key` 回填只覆盖 `external_id` 形如 `feature:<id>:spec|build` 的行。

## 验收

```gherkin
Feature: Pipeline 以 Feature 为先

  Scenario: Tab 顺序 — test_pipeline_tabs_feature_first
    When 打开 /overview
    Then pipeline-tabs 中 Tab 的顺序依次是 Overview、Features、Tasks
    And 访问 /defects 时点亮的是 Features Tab

  Scenario: Overview 的创建按钮默认是 Feature — test_overview_create_defaults_to_feature
    Given 当前用户不是 viewer
    When 打开 /overview
    Then 头部的主创建按钮文字为 "New feature"
    And 按 c 打开的是 NewFeatureDialog
    And 点开 ▾ 能看到 Task、Feature、Defect 三项

  Scenario: Overview 的闭环与面板顺序 — test_overview_loop_is_feature_first test_features_in_flight_total_matches_loop test_features_panel_above_run_next
    When GET /api/pipeline/overview
    Then loop.stages 的 label 依次是 Features、Tasks、Defects
    And features_in_flight.total 等于 Features 阶段 spec 与 building 两部分之和
    And 在 ≥1024px 的页面上，Features 面板位于 RunNextPanel 之上

  Scenario: 单项目作用域下 feature 列表不重复显示 — test_project_scope_lists_features_once
    Given 作用域为项目 P
    When 打开 /overview?project=P
    Then 页面上只有一个 feature 列表

Feature: 项目步骤阶段

  Scenario: 未配置的项目使用默认阶段 — test_unconfigured_project_reads_default_stages
    Given 项目 P 在 project_stages 中没有行
    When GET /api/projects/P/stages
    Then 返回 spec、build 两个 builtin 阶段
    And project_stages 中仍然没有 project_id=P 的行

  Scenario: 新增阶段触发 copy-on-write — test_add_stage_copies_defaults_on_write
    When POST /api/projects/P/stages {key:"design", label:"Design", role:"spec"}
    Then project_stages 中有 P 的 spec、build、design 三行
    And 全局默认行不变

  Scenario: 自定义阶段开步骤并注入提示词 — test_custom_stage_step_injects_stage_prompt
    Given P 的 design 阶段提示词正文为 "只出设计，不写代码 {{stage_label}}"
    And feature F 属于 P
    When POST /api/features/F/steps {stage:"design"}
    Then 生成的 Task 满足 external_id="feature:F:design"、kind="spec"、stage_key="design"
    And 再次 POST 返回同一个 Task（created=false）
    And 该 Task 的 launch prompt 中包含 "只出设计，不写代码 Design"

  Scenario: 已有的步骤 Task 不需要迁移 — test_legacy_spec_step_matches_without_migration
    Given 迁移前已存在 external_id="feature:F:spec" 的 Task T
    When 迁移后执行 POST /api/features/F/steps {kind:"spec"}
    Then 返回 T，created=false

  Scenario: 拒绝未知阶段 — test_unknown_stage_is_rejected
    When POST /api/features/F/steps {stage:"nope"}
    Then 返回 400，field="stage"

  Scenario: builtin 阶段不可删除 — test_builtin_stage_cannot_be_deleted
    When DELETE /api/projects/P/stages/build
    Then 返回 4xx，build 阶段仍然存在

  Scenario: 有 Task 的阶段只能归档 — test_stage_with_tasks_is_archived test_archived_stage_hides_button
    Given design 阶段已有 Task
    When DELETE /api/projects/P/stages/design
    Then design 阶段被归档，而不是被删除
    And F 的 Feature 页不再显示 Design 按钮，已有的 design Task 仍然列出

  Scenario: Feature 页按阶段渲染按钮 — test_feature_page_renders_stage_buttons
    Given P 的阶段顺序为 spec、design、build
    When 打开 /features/F
    Then 步骤按钮依次为 Spec、Design、Build

  Scenario: viewer 只能查看 — test_viewer_cannot_edit_stages
    Given 当前用户是 viewer
    When PATCH /api/projects/P/stages/spec
    Then 返回 403

  Scenario: 生命周期不受影响 — test_custom_spec_role_stage_writes_same_events
    Given F 处于 spec 生命周期阶段
    When F 的 design 步骤（role=spec）开始一次 run
    Then 写入的 feature_events 与当前 spec 步骤开始 run 时写入的完全相同

  Scenario: 项目管理页套用 Pipeline 主题 — test_project_page_wears_the_pipeline_theme
    Given Pipeline 主题选的是 pixel
    When 打开 /projects/P
    Then <html> 带 data-pl-theme="pixel"，面板标题与按键使用 Silkscreen 字体
    And 在项目选择器里选 All projects 会离开到 /overview，而不是改写本页 URL

  Scenario: 项目管理页编辑阶段 — test_project_stages_page_edits_stages
    Given 当前用户不是 viewer
    When 打开 /projects/P，新增阶段 Design 并把它拖到 spec 与 build 之间
    Then 列表顺序为 Spec、Design、Build
    And 展开 Design 行能编辑并保存它的阶段提示词
```

## 闸门

两层检查，不重复：

- **片内（会话自己跑）**：每片的 skill 只跑本片触及的测试——本片新增或修改的 pytest 文件、`npx vite build`、本片相关的 Playwright spec。不跑全量 pytest，也不跑全量 Playwright。
- **Verify 列（会话结束后在 worktree 里跑）**：只检查会话无法自评的事——工作区干净、分支已合回 `f148/integration`、Tests 列里的每个名字都已存在于 `f148/integration`。**不重复跑任何测试。**
- **全 feature 唯一一次汇总**在 S4：跑 F148 触及目录的 pytest（`tests/pipeline tests/features tests/tasks tests/projects tests/prompts`，加上 `tests/tasks/test_schema_parity.py`、`tests/core/test_db_migrate.py`），以及 pipeline 相关 Playwright spec 与 `responsive.spec.js`。全量 pytest / 全量 Playwright 交给 CI，本 feature 不跑。
- 迁移：`regin migrate` 在现有 DB 的副本上执行通过；`regin init` 新建的 DB 与迁移后的 DB 结构一致（由 `test_schema_parity.py` 覆盖）。

## 切片

当前功能面不大，只切 4 片：后端 B、A（以 Feature 为先，前后端一起）、前端 B、一个合并的收尾片。S1 与 S2 互不依赖，可并行。

共用规则：
- 只有 S1 改表（`project_stages` + `agent_tasks.stage_key`），其余片都不加 alembic revision。
- S2 与 S3 都会改 `ProjectScopePanel.vue`（classic 与 `pipeline/themes/pixel/slots/` 两个版本）：S2 去掉 feature 列表，S3 加设置按钮。所以 S3 排在 S2 之后（after）。
- worktree 里先 `ln -sfn /Users/taowang/regin/.venv .venv`，前端片再链 `frontend/node_modules`。Playwright 用本片自己的端口：`REGIN_E2E_API_PORT=837N REGIN_E2E_VITE_PORT=521N REGIN_E2E_SCRATCH=/tmp/regin-e2e-f148sN`（N = 片号）。
- Tests 列的名字原样出现：pytest 函数名，或 Playwright `test('…')` 的标题。
- 所有片都以 `launch: tmux` 启动。

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | 项目步骤阶段：表 + API + run 注入 | 新表 `project_stages` 与 `agent_tasks.stage_key`（alembic `0038_project_stages.py` + `db/schema.sql` + `lib/orm/models/` 三处一起改，`stage_key` 回填 `feature:<id>:spec` 与 `feature:<id>:build` 的行）；建表时写入全局默认阶段 spec、build 及其 `kind='stage'` 的 prompt_templates 行。新模块 `lib/features/stages.py`：按 R1 解析阶段列表、copy-on-write、R7 删除/归档。`web/blueprints/` 新增 `GET/POST /api/projects/<id>/stages`、`PATCH/DELETE …/<key>`、`PUT …/order`（`<id>` 可为 `default`，viewer 写操作 403）。`lib/features/steps.py` 与 `POST /api/features/<id>/steps` 改为接受 `stage`（`kind` 兼容），未知阶段 400 `field="stage"`，Task 的 `kind=stage.role`、`stage_key=stage.key`。`lib/tasks/run_prompt.py` 按 R4 注入阶段提示词（变量加 `stage_label`）。测试放在 `tests/projects/test_stages.py` | B 数据结构, R1, R2, R3, R4, R5, R7, R8 | test_unconfigured_project_reads_default_stages, test_add_stage_copies_defaults_on_write, test_custom_stage_step_injects_stage_prompt, test_legacy_spec_step_matches_without_migration, test_unknown_stage_is_rejected, test_builtin_stage_cannot_be_deleted, test_stage_with_tasks_is_archived, test_viewer_cannot_edit_stages, test_custom_spec_role_stage_writes_same_events | yes | — | `f148/integration` | goal-verified-treenav | test -z "$(git status --porcelain)" && git merge-base --is-ancestor HEAD f148/integration && for t in test_unconfigured_project_reads_default_stages test_add_stage_copies_defaults_on_write test_custom_stage_step_injects_stage_prompt test_legacy_spec_step_matches_without_migration test_unknown_stage_is_rejected test_builtin_stage_cannot_be_deleted test_stage_with_tasks_is_archived test_viewer_cannot_edit_stages test_custom_spec_role_stage_writes_same_events; do git grep -q -w "$t" f148/integration -- tests frontend/tests && continue; echo "missing $t"; exit 1; done |
| S2 | 以 Feature 为先 | `lib/pipeline/overview.py`：`_loop` 的 stages 改为 Features、Tasks、Defects，新增 `features_in_flight`（spec + building，按最近活动倒序，上限 5，所有作用域都返回）。`PipelineView.vue` 的 `TABS` 与 `CACHED_TABS` 改为 Overview、Features、Tasks（`/defects` 仍点亮 Features）。Overview：新的 Features 面板与 `NeedsDecisionPanel` 组成第一行，`RunNextPanel` 移到下方占满整行，<1024px 顺序为 Features、Needs decision、Run next；右上角 `PipelineCreateButton kind="feature"`；单项目作用域下 `ProjectScopePanel`（classic 与 pixel 两版）只保留项目信息头。改写受影响的 pipeline Playwright spec，新增 `frontend/tests/pipeline-feature-first.spec.js`；后端测试放在 `tests/pipeline/test_overview.py` | A 决策表, A Features 面板 | test_pipeline_tabs_feature_first, test_overview_create_defaults_to_feature, test_overview_loop_is_feature_first, test_features_in_flight_total_matches_loop, test_features_panel_above_run_next, test_project_scope_lists_features_once | — | — | `f148/integration` | goal-verified-treenav | test -z "$(git status --porcelain)" && git merge-base --is-ancestor HEAD f148/integration && for t in test_pipeline_tabs_feature_first test_overview_create_defaults_to_feature test_overview_loop_is_feature_first test_features_in_flight_total_matches_loop test_features_panel_above_run_next test_project_scope_lists_features_once; do git grep -q -w "$t" f148/integration -- tests frontend/tests && continue; echo "missing $t"; exit 1; done |
| S3 | 项目管理页 + Feature 页阶段按钮 | 新路由 `/projects/:id`（含 `/projects/default`），挂在 Pipeline shell 下、不点亮 Tab、不进 KeepAlive；页面含 Stages 分区（新增、改名、排序、自定义阶段设 role、归档/删除，行展开为基于 `PromptBody` 的提示词编辑器）与只读基本信息（链到 `/repos/:name`）；viewer 隐藏编辑控件。入口：`ProjectPicker` 的「Project settings…」与 `ProjectScopePanel` 头部的「Project settings」按钮。`FeatureTasks.vue` 按 `position` 为每个未归档阶段渲染按钮；`FeatureStepDialog.vue` 显示阶段 label 与只读提示词预览，请求体发 `stage`。新增 e2e `frontend/tests/project-stages.spec.js` | 项目管理页, R6, R7, R8 | test_project_stages_page_edits_stages, test_feature_page_renders_stage_buttons, test_archived_stage_hides_button | — | S1, S2 (after) | `f148/integration` | goal-verified-treenav | test -z "$(git status --porcelain)" && git merge-base --is-ancestor HEAD f148/integration && for t in test_project_stages_page_edits_stages test_feature_page_renders_stage_buttons test_archived_stage_hides_button; do git grep -q -w "$t" f148/integration -- tests frontend/tests && continue; echo "missing $t"; exit 1; done |
| S4 | 收尾：integrate + migrate + verify | 确认 S1–S3 都已合回 `f148/integration`、没有零 commit 的片，冲突就地解决。在现有 DB 的副本上（`sqlite3 .backup`，经 `REGIN_TRACE_DB_PATH` 指向）跑 `regin migrate` 与 `doctor`，确认无 schema finding。跑一次 F148 范围的汇总测试（见闸门：触及目录的 pytest + pipeline 相关 Playwright + `responsive.spec.js`），不跑全量。最后起真实应用（spare port 上的 `regin serve` + vite），走一遍 /overview、/features/F、/projects/P，截图到 `/tmp/f148-product/`，经 `send_to_user` 汇报；发现问题就地修 | 验收, 闸门 | test_every_slice_merged_back, test_migrate_leaves_no_schema_finding | — | S3 | `f148/integration` | run | test -z "$(git status --porcelain)" && git merge-base --is-ancestor f148/s1 f148/integration && git merge-base --is-ancestor f148/s2 f148/integration && git merge-base --is-ancestor f148/s3 f148/integration && git merge-base --is-ancestor HEAD f148/integration |




