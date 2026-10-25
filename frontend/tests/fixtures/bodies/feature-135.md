# Feature详情页面 Task/Session 关联展示

## Intent
Feature 详情页（`/features/:id`）的 Tasks 卡和 Sessions 卡原本各画各的：task 行看不到自己跑出的 session 和用量；
session 行的标题多半只是一行 skill 调用（`/goal-verified-treenav`），N 次运行读起来一模一样。
目标：task 行直接带出它的 session 与用量，session 行读作它所服务的 task —— 两张卡说的是同一批 session。

Non-goals: 不在 Feature 页加 Arm/Run 控件（仍在 task 页）；不改 session 本身的标题（`sessions.title` 不写）；
不把 Sessions 卡拆成按 task 分组（它仍是 figures 所求和的完整证据列表）。

## Shape
```ts
// GET /api/features/:id → sessions.rows[]   (lib/features/ledger.py SessionRow.to_dict)
type SessionRow = {
  trace_id: string; short_trace: string; date: string
  title: string                  // sessions.title, 原样
  command: string | null         // title 整体恰为一个 slash 命令时 = 该命令，否则 null
  display_title: string          // command && task_title ? task_title : title
  task_id: number | null         // 产生该 session 的 run 的 task
  task_title: string | null      // agent_tasks.title；task 已删除 → null
  cost_usd: number | null        // 非 primary → 0.0（记在其 primary feature 下）
  provenance: 'declared' | 'recalled' | 'run'; round: number | null; is_primary: boolean
}
```

## Rules
| 条件 | task_id |
|---|---|
| run lane 行 | 该 run 的 task |
| declared/recalled 行，且同一 trace 也由某 task 的 run 产生 | 该 run 的 task（provenance 不变） |
| 多个 task 的 run 指向同一 trace | `ORDER BY trace_id, task_id` 的第一个（确定性） |
| 其它 | null |

Task 行用量汇总 `costSum(rows)`（frontend/src/utils/ledgerFormat.js），只计 `is_primary` 行：
| 行状态 | 显示 |
|---|---|
| 0 行 | 不显示 toggle |
| 全部非 primary | `charged elsewhere` |
| primary 行全无 usage | `no usage` |
| 部分 primary 行无 usage | `≥ $X`（限定符在数字里） |
| 全有 usage | `$X` |

## Invariants
- I1 Task 行的 session 分组取自 `ledger.sessions.rows` —— 与 Sessions 卡同一数组，不另发请求。
- I2 绝不以 `$0.00` 表示缺失的 usage。
- I3 trace 链接仅 admin 可点（与 Sessions 卡一致）；非 admin 显示纯文本 id。
- I4 `regin feature ledger` CLI 的 sessions 列表打印 `display_title`。

## Acceptance
```gherkin
Scenario: task 行汇总并展开其 sessions
  Given task #7 有两个 run session，一个 $1.50，一个无 usage
  When 打开 /features/:id
  Then task #7 行显示 "2 sessions · ≥ $1.50"，aria-expanded=false
  When 点击它
  Then 展开两行：date · short trace · 标题或命令 · 成本（"no usage" 为斜体琥珀色）
  And 没有 run session 的 task 不显示 toggle

Scenario: skill 行 session 读作其 task
  Given session 标题为 "/goal-verified-treenav"，由 task #7 "Build · the page" 的 run 产生
  Then Sessions 卡该行标题为 "Build · the page"，旁边命令 chip "/goal-verified-treenav"，并链接 task #7
  And 标题为自然语言的 session 保留原标题且无命令 chip
  And 无 task 的 "/feature-pipeline" session 标题仍为 "/feature-pipeline"

Scenario: 手机宽度
  Given 视口 390px，展开一个 task 的 sessions
  Then .content-scroll 不横向滚动
```
Tests: `tests/features/test_ledger_task_sessions.py`, `frontend/tests/feature-task-sessions.spec.js`.
