# Feature #143 — Pipeline 操作逻辑优化（四期）：多 defect 合并修复、完成即一致、feature ↔ branch 绑定

> 原始需求：① 多个 defect 合并创建一个 task 时，prompt 标题只含第一条 defect，后续 link 的 defect 信息缺失；要求操作简便。② session 已报告完成，defect 却仍 open 且无状态更新；要求简单可靠地阻止。③ feature 的 worktree / branch 在页面上看不到，arm 时靠人选；参考他家做法，太复杂就不做，要求可靠。④ 排查同类问题一并纳入。
>
> Spec written 2026-10-07 against `master` @ `d6a32db1`.

## Intent

一个 fix task 修几个 defect，**它的 run 就被告知所有这些 defect**，而且标题能让人一眼看出它修的是哪几个。fix task 在任何结算路径上都**不会**在它的 defect 仍 open 时被记为 `done`；人为造成的不一致（Mark done、reopen）**看得见**，并且一次点击就能处理。feature 用的是哪个 branch，在 feature 页面上**可以直接看到**，arm 时**预先填好**，不用再手选。

## Root cause (as found in code)

| # | Symptom | Code today | Gap |
|---|---|---|---|
| A1 | 多 defect 共用一个 task，prompt 只知道第一条 | `fix_items.create_for` 写入 `title=symptom[:200]` 和 `body=task_body(第一条 defect)`；后来 link 的 defect 只改了 `defects.fix_task_id`（`store.set_fix_item`）。`run_prompt._cited_refs` 只读 task body 和 spec 里的 `@` 引用 | 后续 defect 不在 body 里，所以 `<references>` 里也没有。run 只能从 `<finish>` 的 close 命令里看到它们的 **id**，而且只有仍 open 的那些。线上实例：task #375「修复所有关联defects」（body 为空）挂了 3 个 defect，它的 run 拿到的 `<references>` 是空的 |
| A2 | 合并操作不顺手 | 一次只能处理一个 defect：先在 defect A 上点 `Create fix task`，再到 B、C 上点 `Link existing task`，**手敲** task id（`defectForms.js` 的 `fix-item`） | 没有选择器，也没有一次处理多个 defect 的入口 |
| A3 | 第二个 defect 不能再点 Create | `_is_own_task` 要求 `external_id == f"defect:{row.id}"` | 对 B 来说，A 创建的 task 被视为"外来的"，`create_for(B)` 会报错 |
| B1 | 结束了却没报告，task 仍被记为 `done` | `handle_completion` → `_settled(outcome)`：`exited` ⇒ `RUN_STATUS_DONE` → `_settle_and_fire` → `complete_run(task_status=done)`。只有 `finish_run` 调用了 `_refuse_with_open_defects` | 有 SDK clean exit 或 Stop、却没有 `task finish` 的 run，会绕过检查（lesson 9c0a962b）。`_settle_and_fire`（runs.py:927）是 run 写入 `done` 的唯一位置，但它不做检查 |
| B2 | task 是 done，defect 是 open，系统不提示 | `store.reopen` 只改 defect；`_after_status`（Mark done）有意不关 defect | 线上 2 例：defect #242（round 2）→ task #345 `done`；defect #229 → task #300 `archived`。没有任何 UI 状态显示这种不一致，defect 行上 `Create fix task` 也被隐藏（因为 `fix_task_id` 已设） |
| C1 | 看不到 feature 的 branch | branch 只存在 `agent_task_runs.params_json.worktree.{branch,base}`。slice 的约定 `f{id}/s{n}`、base `f{id}/integration`（`feature_slices.default_tail_base`）只在 arm proposal 里出现 | `features` 没有 branch 字段，feature 页面没有 branch 信息。非 slice 的 task（fix / spec / 单条 task）arm 时 base 为空，要靠人填 |

## Prior art (C)

| Product | Binding | Reliability |
|---|---|---|
| GitHub Issues "Development" | 从 issue 创建 branch，显式存储链接 | 显式，最可靠 |
| Linear | 复制 `user/abc-123-title` 分支名；按分支名里的 issue id 自动关联 | 依赖命名，人改名就断 |
| Jira dev panel | 在 branch / commit 名里找 issue key | 同上 |

**采用 GitHub 式的显式绑定**。regin 自己已经有显式记录：每个 run 的 `params_json.worktree`。所以 feature 的 branch 列表**从 run 记录派生**，不解析分支名。另外只加一个可编辑的字段 `features.branch`（integration 分支），用来预填 arm。

## Shape

```ts
// A: multi-defect fix task
POST /api/defects/fix-task   { defect_ids: number[] }           // ≥1, all same feature
  → 200 { task_id: number, defects: DefectView[] }
  → 409 { error, blocking: number[] }                            // some defect already points at another task / unattributed / features differ

type FixTaskTitle =                       // regin-generated; recomputed on link/unlink while auto
  | `${string}`                           // 1 defect: symptom[:200]  (unchanged)
  | `Fix #${number}, #${number}…`         // ≥2: ids ascending only — no symptom (a cut-off symptom read as the whole task; the body cites each defect); total ≤200

// agent_tasks: + auto_title TEXT NULL — the title regin last generated; title == auto_title ⇔ still regin's

// C: feature ↔ branch
// features: + branch TEXT NULL — the feature's integration branch, operator-set
type FeatureBranches = {               // GET /api/features/:id/branches — live git read on request, no cache
  integration: { name: string; source: 'set' | 'convention' | null;
                 exists: boolean; merged_into_default: boolean | null }
  runs: Array<{ branch: string; base: string; task_id: number; last_run_id: number;
                worktree_path: string | null;   // from `git worktree list --porcelain`
                exists: boolean; merged_into_base: boolean | null }>
}
```

## Rules

### A — 多 defect → 一个 fix task

| Rule | Condition | Result |
|---|---|---|
| A-R1 | 解析 run 的 `<references>` 时 | 引用集合 = body 引用 ∪ spec 引用 ∪ **`by_fix_item(task_id)` 的每一个 defect**（不论 open/closed，按 id 升序，去重）。在 launch 时实时解析，不写进 body |
| A-R2 | `POST /api/defects/fix-task` 收到 N≥1 个 defect | 全部 attributed 到同一个 feature，且都没有 `fix_task_id`（或已经指向本次批量得到的同一个 task）→ 只建 **一个** `kind=fix, source=defect` 的 task；`external_id = defect:<最小 id>`，所以重复提交会解析到同一个 task。全有或全无 |
| A-R3 | A-R2 前置条件有任何一条不满足 | 409，`blocking` 列出违规的 defect id，**不写任何东西** |
| A-R4 | 任何 link / unlink（`set_fix_item`、`clear_fix_item`、A-R2、`merge` 删掉指向它的重复 defect）之后，task 的 `title == auto_title` | 重算标题：1 个 defect → `symptom[:200]`；≥2 个 → `FixTaskTitle`。`auto_title` 一起更新 |
| A-R5 | 同上，但 `title != auto_title`（人改过标题） | 标题不动 |
| A-R6 | body | 创建时以 `@` 引用列出选中的**每一个** defect（`- [D<id> …](defect:<id>)`，按 id 升序）和 feature，不复制 symptom；生成后不再重写，之后 link 的 defect 只经 A-R1 进入 prompt |
| A-R7 | 判断"defect 拥有这个 task"（`_is_own_task`） | `task.source == 'defect' ∧ task.id == row.fix_task_id`；不再要求 `external_id` 是这条 defect 自己的 id |
| A-R8 | defect 的截图 | 不复制到 task；run 的 `<references>` 里该 defect 一行写明截图编号和 `regin defects image <id> <N>`，由 agent 自己取 |

UI（全部在 `FeatureDefects` 卡片上，范围限定为一个 feature，所以不涉及归因）：
- 只有 open 且未 link 的 defect 行显示复选框；选中 ≥1 个时出现 `Create one fix task (N)`，调用 A-R2。
- `Link existing task` 不再手敲 id，改为下拉，选项是本 feature 中状态不是 `done`/`archived` 的 task（`#id title`）。仍保留"输入 id"的选项。
- 其它列表（`/defects`、全局列表）不加复选框。DefectActions 里"不做批量归因"的规则保持不变。

### B — 完成即一致

| Rule | Path | Condition | Result |
|---|---|---|---|
| B-R1 | `finish_run(status=done)` | 有 open 的 fix defect | 保持现状：拒绝，返回 close 命令 |
| B-R2 | `_settle_and_fire`（所有 run 结算：`handle_completion` clean exit、Stop、verify 通过、CLI 结算） | 结算后的状态是 `done`，且 `_open_fix_defects(task_id)` 不为空 | 改记为 `failed`，`exit_reason = "ended with defect #… still open"`；`fire_ready_dependents` 照常按 failed 处理（不放行依赖） |
| B-R3 | 人点 Mark done（`update_task`） | 有 open 的 fix defect | 保持人工覆盖：确认框列出这些 defect（现状）。之后由 B-R4 显示出来 |
| B-R4 | 任何读取方 | defect `open` ∧ 其 fix task 处于 `done`/`archived` | defect 行显示 `fix done · still open` 徽标；动作区重新出现 `Reopen fix task`（task `done → idea`，现有转换）和 `Create fix task`（`create_for` 见到 drift 时新建一个 task，`external_id = defect:<id>:after:<旧 task id>`，并改指向它；再点一次解析到同一个新 task） |
| B-R5 | `store.reopen(defect)` | fix task 是 `done` | **不**自动改 task（regin 不从一侧写另一侧）。B-R4 的徽标就是提示 |

不变量：
- **I-B1**：在 B-R2 生效之后，没有任何 run 结算会在 `_open_fix_defects(task_id) ≠ ∅` 的情况下把 task 写成 `done`。只有人工 Mark done 可以（会被 B-R4 显示出来）。
- **I-B2**：`fix done · still open` 的数量 = `count(defects d JOIN agent_tasks t ON t.id = d.fix_task_id WHERE d.status='open' AND t.status IN ('done','archived'))`，UI 显示的数字与这个查询一致。

### C — feature ↔ branch

| Rule | Condition | Result |
|---|---|---|
| C-R1 | `features.branch` 不为空 | integration = 它，`source='set'` |
| C-R2 | 为空，且 `refs/heads/f{id}/integration` 在该 feature 的 repo（`feature_slices._repo_root`）里存在 | integration = `f{id}/integration`，`source='convention'` |
| C-R3 | 都不满足 | `integration.name=''`，`source=null` |
| C-R4 | `runs` 列表 | 取该 feature 下所有 task 的 run 中，`params_json.worktree.branch` 的不同值，各附最后一次 run。是否存在、worktree 路径、是否已合入 base（`git merge-base --is-ancestor`），都在请求时实时读取 git |
| C-R5 | arm 一个 `feature_id` 不为空的非 slice task（TaskArmSheet） | base 预填为 integration name（C-R1/R2）；为空则不预填（保持现状）。优先级在 R4b 之后：pending attempt、上一次 run 的 base 先于 integration。slice proposal 不变（Invariant 3：base 只取自 slice 表）。worktree 选择器会把所选 repo 里不存在的 base 换成默认 base，这时表单显示提示（`This feature integrates on X; the worktree forks from Y.`），不静默 |
| C-R6 | 设置 branch | feature 页面上的 Branch 一栏可以编辑，`PATCH /api/features/:id {branch}`；值用 `git check-ref-format --branch` 校验（回显须与输入一致，所以 `@{-N}` 被拒绝），不要求 branch 已存在；`null` 或空串表示清除；branch 不合法时整个 PATCH 返回 400 `field=branch`，其它字段也不写 |
| C-R7 | git 读取失败（repo 缺失、不是 git 仓库、git 超时） | 端点返回 200，内容为 `integration.exists=false`、`runs=[]`，外加 `error` 字符串；已 set 的 `integration.name`（`source='set'`）照常返回，convention 无法判定时为空；页面显示这个错误，不报 500。`params_json` 形状异常的 run 行直接跳过，不影响整个读取 |

Schema（每项两处修改）：`alembic/versions/00NN_*.py` + `db/schema.sql` + `lib/orm/models/`，涉及 `features.branch` 和 `agent_tasks.auto_title`。现有 `source='defect'` 的 task，迁移时设 `auto_title = title`。

## 同类问题排查（已纳入）

| # | Finding | Covered by |
|---|---|---|
| S1 | 人工建的 task（`source=web`）挂了 defect，但 body 里没有 `@defect`，run 看不到 symptom（#375） | A-R1 |
| S2 | Stop 一个停在空闲状态、没有报告过的 run，task 会被记成 `done`，open defect 被忽略 | B-R2 |
| S3 | defect reopen 后，fix task 仍是 done，界面无提示，`Create fix task` 也被隐藏（#242→#345，#229→#300） | B-R4 |
| S4 | 第二个 defect 不能对 A 的 task 点 Create（`_is_own_task` 绑定在 external_id 上） | A-R7 |
| S5 | 非 slice task arm 时 base 为空，可能 fork 自 master，缺少 feature 的代码（lesson edb3a6b5） | C-R5 |

## Non-goals

- 不按分支名自动关联（Linear / Jira 式）。不创建、删除、合并 branch，regin 只读 git。
- 不做 PR / 远端状态。
- run 结算时不自动关闭 defect；关闭仍要求 `--check`（missing_check 只有关闭的人能给）。
- 全局 `/defects` 列表不做批量操作，也不做批量归因。
- reopen 时不自动改 task 状态（B-R5）。

## Acceptance

```gherkin
Feature: multi-defect fix task

  Scenario: every linked defect reaches the run's references
    Given task T with body "修复所有关联defects" and no @-refs
    And defects #1 (open) and #2 (closed) have fix_task_id = T
    When the launch prompt for a run of T is built
    Then its <references> block has one line for defect #1 and one for defect #2, each carrying its symptom

  Scenario: one task from three defects
    Given open, unlinked defects #10, #11, #12 attributed to feature F
    When POST /api/defects/fix-task {defect_ids:[12,10,11]}
    Then exactly one agent_tasks row is created, kind=fix, source=defect, feature_id=F
    And all three defects have fix_task_id = that task
    And its title is "Fix #10, #11, #12"
    And repeating the request returns the same task_id and creates no row

  Scenario: batch is all-or-none
    Given #10 is unlinked and #13 points at task 99
    When POST /api/defects/fix-task {defect_ids:[10,13]}
    Then 409 with blocking=[13] and #10.fix_task_id is still null

  Scenario: linking a second defect retitles an auto-titled task
    Given task T created from defect #10 with title == auto_title
    When defect #11 is linked to T
    Then T.title == "Fix #10, #11"
    When an operator renames T to "x" and defect #12 is linked
    Then T.title == "x"

  Scenario: Create on a defect already on a defect-sourced task
    Given defect #11 linked to task T (source=defect, external_id "defect:10")
    When fix_items.create_for(11)
    Then it returns #11 pointing at T and raises nothing

Feature: done is consistent with defects

  Scenario: clean exit with an open fix defect
    Given task T is the fix for open defect #5 and its run never called task finish
    When handle_completion receives outcome status "exited"
    Then the run settles failed with exit_reason containing "#5"
    And T.status == "failed" and no dependent of T fires

  Scenario: clean exit after the defect is closed
    Given defect #5 closed with a missing_check
    When handle_completion receives "exited"
    Then T.status == "done"

  Scenario: drift is visible
    Given defect #242 open with fix task #345 done
    Then the defect row shows "fix done · still open" and a "Reopen fix task" action
    And the count of such rows equals the I-B2 query

Feature: feature branch

  Scenario: convention branch is shown
    Given feature F with branch NULL and refs/heads/fF/integration existing
    When GET /api/features/F/branches
    Then integration == {name:"fF/integration", source:"convention", exists:true}

  Scenario: run branches are derived from run params
    Given two runs of F's tasks armed with worktree.branch "fF/s1" and "fF/s2"
    Then runs[] lists both, each with its last_run_id and live exists / merged_into_base

  Scenario: arm prefill
    Given feature F with branch "release/x" set
    When the arm sheet opens for a non-slice task of F
    Then the base field reads "release/x"

  Scenario: bad repo does not 500
    Given F's project repo path does not exist
    Then GET /api/features/F/branches is 200 with runs=[] and an error string
```


## 切片表

公共约定：
- 所有 worktree 的 Verify 都以 `ln -sfn /Users/taowang/regin/.venv .venv` 开头；前端切片再链接 `frontend/node_modules`。
- 每片的 skill 在任务内只跑本片的 scoped 测试；全量 pytest 只在 S9 跑一次。
- schema（`features.branch`、`agent_tasks.auto_title`）只由 S1 改，其它切片不得新增 alembic revision。

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | schema：features.branch 与 agent_tasks.auto_title | `alembic/versions/0037_*.py` + `db/schema.sql` + `lib/orm/models/`：新增 `features.branch TEXT NULL` 与 `agent_tasks.auto_title TEXT NULL`；迁移时对现有 `source='defect'` 的 task 设 `auto_title = title`；fresh install 与 migrate 后的 schema 一致；测试放在 `tests/defects/test_fix_task_schema.py` | Shape, Schema | test_features_branch_column_exists, test_agent_tasks_auto_title_backfilled_for_defect_tasks, test_schema_sql_matches_alembic_head | yes | — | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/defects/test_fix_task_schema.py tests/defects/test_defects_schema_parity.py |
| S2 | 多 defect fix task（后端） | `lib/tasks/run_prompt.py`：A-R1，`<references>` 并入 `by_fix_item(task_id)` 的全部 defect（open/closed，按 id 升序去重，launch 时实时解析）；`lib/defects/fix_items.py` + `lib/defects/store.py`：A-R2/A-R3 批量创建（全有或全无，`external_id=defect:<最小id>`，重复提交幂等），A-R4/A-R5 在 link/unlink 后按 `auto_title` 重算标题，A-R7 改 `_is_own_task`，A-R6 body 引用全部 defect，A-R8 截图留在 defect 上；`POST /api/defects/fix-task`（200 / 409 + blocking）；测试放在 `tests/defects/test_multi_defect_fix_task.py` | A-R1, A-R2, A-R3, A-R4, A-R5, A-R6, A-R7, A-R8, S1, S4 | test_every_linked_defect_reaches_the_run_s_references, test_one_task_from_three_defects, test_batch_is_all_or_none, test_linking_a_second_defect_retitles_an_auto_titled_task, test_create_on_a_defect_already_on_a_defect_sourced_task, test_batch_body_cites_every_defect_and_copies_no_image, test_a_launch_names_how_to_fetch_a_linked_defect_screenshot | | S1 | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/defects/test_multi_defect_fix_task.py tests/defects/test_fix_item.py tests/defects/test_web_fix_item.py |
| S3 | 完成即一致（后端） | `lib/tasks/runs.py`：B-R2，`_settle_and_fire` 结算为 done 而 `_open_fix_defects(task_id)` 非空时改记 failed，`exit_reason` 写明 `ended with defect #… still open`，依赖不放行；B-R4/I-B2：defect 视图（列表与 feature 卡片的 API）带 `fix_drift`（defect open ∧ fix task done/archived），并提供与 I-B2 查询一致的计数；B-R1/B-R3/B-R5 保持现状；测试放在 `tests/defects/test_done_consistency.py` | B-R1, B-R2, B-R3, B-R4, B-R5, I-B1, I-B2, S2, S3 | test_clean_exit_with_an_open_fix_defect, test_clean_exit_after_the_defect_is_closed, test_drift_is_visible, test_stop_idle_run_with_open_defect_settles_failed | | — | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/defects/test_done_consistency.py tests/defects/test_fix_task_finish.py |
| S4 | feature ↔ branch（后端） | `lib/features/`：`GET /api/features/:id/branches` 按 C-R1–C-R4 实时读 git（integration 的 set/convention、runs 由 `params_json.worktree.branch` 派生、`git worktree list --porcelain`、`merge-base --is-ancestor`）；C-R7 repo 缺失时 200 + `error`；`PATCH /api/features/:id {branch}` 用 `git check-ref-format --branch` 校验；arm 预填所需的 integration name 也由该端点给出；测试放在 `tests/features/test_feature_branches.py`（用临时 git 仓库） | C-R1, C-R2, C-R3, C-R4, C-R6, C-R7, Non-goals | test_convention_branch_is_shown, test_run_branches_are_derived_from_run_params, test_bad_repo_does_not_500, test_set_branch_validates_ref_format | | S1 | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/features/test_feature_branches.py |
| S5 | FeatureDefects 卡片（前端 A+B） | `frontend/src/components/features/FeatureDefects.vue` + `frontend/src/components/defects/defectForms.js` + `DefectActions.vue`：open 且未 link 的行显示复选框，选中 ≥1 时出现 `Create one fix task (N)` 调 `POST /api/defects/fix-task`，409 时就地显示 blocking；`Link existing task` 改为下拉（本 feature 中非 done/archived 的 task，保留输入 id）；drift 行显示 `fix done · still open` 徽标与 `Reopen fix task` / `Create fix task`；全局 `/defects` 不加复选框；新增 e2e `frontend/tests/feature-defects-batch.spec.js` | A UI, B-R4 | test_feature_defects_batch_create_one_fix_task, test_link_existing_task_offers_dropdown, test_drift_row_shows_badge_and_reopen, test_global_defects_list_has_no_checkboxes | | S2, S3 | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && cd frontend && npx vite build && REGIN_E2E_API_PORT=8361 REGIN_E2E_VITE_PORT=5201 REGIN_E2E_SCRATCH=/tmp/regin-e2e-f143s5 ./node_modules/.bin/playwright test feature-defects-batch |
| S6 | Branch 一栏与 arm 预填（前端 C） | feature 页面（`FeatureLedgerView.vue` 或其子组件）新增 Branch 一栏：integration（可编辑，PATCH）+ runs 列表（branch、last run、worktree、exists、merged），C-R7 的 `error` 显示在栏内；`TaskArmSheet.vue`：非 slice 且有 `feature_id` 的 task，base 预填 integration name，为空则不预填；slice proposal 不变；新增 e2e `frontend/tests/feature-branch.spec.js` | C-R5, C-R6, C-R7 | test_arm_prefill, test_feature_page_shows_branch_panel, test_branch_panel_shows_git_error | | S4 | `f143/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && cd frontend && npx vite build && REGIN_E2E_API_PORT=8362 REGIN_E2E_VITE_PORT=5202 REGIN_E2E_SCRATCH=/tmp/regin-e2e-f143s6 ./node_modules/.bin/playwright test feature-branch task-arm |
| S7 | integrate | 检查 S1–S6 都已合回 `f143/integration`，没有零 commit 的切片；合并冲突就地解决 | Acceptance | test_every_slice_merged_back | — | S5, S6 | `f143/integration` | resolving-merge-conflicts | git merge-base --is-ancestor f143/s1 f143/integration && git merge-base --is-ancestor f143/s2 f143/integration && git merge-base --is-ancestor f143/s3 f143/integration && git merge-base --is-ancestor f143/s4 f143/integration && git merge-base --is-ancestor f143/s5 f143/integration && git merge-base --is-ancestor f143/s6 f143/integration |
| S8 | migrate + doctor | 在 `f143/integration` 上对现有库的**副本**跑 `regin migrate`（`sqlite3 .backup` 后经 `REGIN_TRACE_DB_PATH` 指向副本），确认 `features.branch` / `agent_tasks.auto_title` 落地、defect task 的 `auto_title` 已回填，doctor 无 schema finding。live 库（`/Users/taowang/regin/db/regin.db`）由 master 所在的主 worktree 共享，不在本分支 migrate：stamp 成 0037 后 master 解析不了该 revision；它在 f143 合入 master 后由主 worktree 跑 `regin migrate`，届时 `test_migrate_leaves_no_schema_finding` 才真正执行（在 worktree 里按设计 skip） | Schema | test_migrate_leaves_no_schema_finding | — | S7 | `f143/integration` | run | sqlite3 /Users/taowang/regin/db/regin.db ".backup '$TMP/regin-copy.db'" && REGIN_TRACE_DB_PATH=$TMP/regin-copy.db /Users/taowang/regin/.venv/bin/python cli/regin.py migrate && REGIN_TRACE_DB_PATH=$TMP/regin-copy.db /Users/taowang/regin/.venv/bin/python cli/regin.py doctor |
| S9 | verify | 在 `f143/integration` 上跑一次全量 pytest（全 feature 唯一一次）与本期两个 e2e；失败就地修复 | Acceptance, I-B1, I-B2 | test_full_suite_passes | — | S8 | `f143/integration` | run | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q -n auto --dist=loadfile |







