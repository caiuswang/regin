`wayfinder:map` —— 这一行是地图标记，不要删。

## Destination

一份**可交付的端到端流水线设计**：一个大需求进来，经 `feature + spec` → planner 产出 task graph 草案 → **人批准后才 arm** → 并行执行 → 集成 → 验证 → ship，中途冒出的缺陷有结构性闸门兜住。

终点是**决策集**，不是实现。这张图走完时，每个 ticket 都有一条 resolution，实现另开 feature。

## Notes

- **领域**：regin 自己的 pipeline —— `lib/features/`、`lib/tasks/`（`deps.py` 的 DAG + `runs.py:832` 的 autorun 级联）、`lib/defects/`、前端 Pipeline 页。
- **每个 session 必用**：`/grilling`、`/domain-modeling`；答案按 `docs/spec-notation.md` 的记法写（类型 / 状态机 / 决策表 / 不变量 / Gherkin），散文只留意图与非目标。
- **对话用中文**，表格与类型定义可中英混排。
- **人机边界已定**：planner 产出草案，**人批准后才 arm**。capture invariant 不破 —— 捕获的 task 是惰性的。
- **不写 `docs/` spec 文件**；决策活在 ticket 的 resolution comment 里，地图只留一行索引。
- **不用时钟启发式**（skew window / 轮询 / setTimeout 重试）。要标识符驱动或事件驱动。
- **先查成熟方案再自造** —— 这是站着的偏好，W2 就是为它设的。
- 地图和 ticket 就在 regin 自己身上（feature = map，task = ticket，`agent_task_deps` = blocking，`tags_json` 带 `wayfinder:<type>`，`assignee` = claim）—— 顺带压测被改造的对象。
- **regin 是多项目 harness，每个决策都要分清「通用基质」与「项目特有内容」。** 8 个已注册仓库，`pattern_deployments` 29 条分布在 4 个项目上，至少两个项目是 TypeScript。`docs/cross-project-portability-audit-2026-09-02.md` 的结论逐字如此：**基质是真正跨项目的，投递和 skill/agent 的内容不是。** 一条只对 regin 成立的规则，要么写成"项目自己导出"的方法，要么明确标注为 regin 的实例 —— 别当成结论。W10 就是因为违反这一条被重新取景的。

## 为什么需要这张图（事实，不是感受）

| 步骤 | 今天由谁做 | 证据 |
|---|---|---|
| 建 feature + spec | agent（`/feature-pipeline`） | `features` 73 行，spec 在 `features.body` |
| spec → 验收项（测试名） | agent，靠 skill 提醒 | f73 spec 有 22 个命名测试 |
| **拆 slice → task graph** | **人手打指令** | `"Build a task graph to let it executed sequently"`，2026-09-16 05:35 与 05:03 |
| **每条 slice 的 build prompt** | **人手写散文** | S1–S9 各一段 `"Spec: regin feature spec 73 — section R5 …"` |
| **分支 / worktree / 集成拓扑** | **没人建模** | defect 152、153 |
| 依赖满足后开跑 | 机器，已自动 | `lib/tasks/runs.py:832` |
| **每条 slice 的 verify prompt** | **人手写散文 ×10** | `"You did NOT write this code…"` |
| 落地后迁 DB | 无人 | defect 155：live DB 落后 alembic head |
| ship | 人 | `regin feature ship` |

一句话：**DAG 的执行器已经有了，DAG 的作者和拓扑没有。**

## Decisions so far

<!-- 索引：一条 closed ticket 一行。详情在 ticket 的 resolution comment 里，这里只留够判断相关性的一句。 -->

- **W1 · 43 个 defect 的根因分类与缺失闸门排序**（task #109，`research/w1-defect-root-causes`）—— 代码逻辑 18 / UI 10 / 规格 9 / 流程集成 6；13 道闸门排序，头两名 **G-RESP**(8) 与 **G-STATE**(7)。两条改变后续 ticket 的结论：**流程/集成类从不逃逸**（缩短轮数，不改变 ship 质量，修正了 W5 的前提），以及 **escape 指标目前大半是 backfill 假象**（106 条事件里 90 条一瞬间铸出，可信 escape 只有 2 个）。
- **W2 · 现成方案调研：plan-approve-execute 的先例**（task #110，`research/w2-planner-prior-art`，9 系统 / 105 条一手来源）—— **边模型是已成定论的先例，regin 已经吻合**（Linear 与 GitHub 各自收敛到 parent vs blocked-by 两条关系）；**待批准的 plan 谁都没有** —— 九个系统全都只在接受之后才存图，GitHub Copilot Workspace 原样建过这个循环并于 2025-05-30 下线。可借 6 个形状（DraftIssue 的提案时铸 id、Linear 批量原子落地、Airflow `hitl_detail` 的 `responded_at IS NULL` 即待批、Prefect 的 JSON Schema 校验、Dagster 的 graph/plan 内容哈希两级、Airflow 的逐节点版本钉住），必造 4 件（草案实体 / 批准状态机 / 同时吐节点与边的 planner / spec 变更后的失效规则）。
- **W3 · Plan 是什么类型，存在哪**（task #111）—— **不造 plan 实体。plan 就是「一批 `idea` 态的 task + 它们的边」，批准就是 arm，零张新表。** 拆图由一个 `/plan-feature` skill 做（建完图停在 `idea`），arm 是另一条命令 —— 唯一的人工关卡不放进 skill 内部分支。认下三条代价：无「我批的是哪张图」的记录、未批准草案混进 `/tasks`、spec 改了不提示图过期。**先证明循环有用再建实体**；升级触发条件见 Not yet specified。
- **W4 · slice 的边界由什么决定**（task #112）—— **切分归 spec，不归 planner。** 两段 skill：第一段把 slice 表（`# | Slice | Delivers | Depends on` + 依赖图）写进 `features.body`，第二段**机械转录**成 task 与边、不做任何判断。一片 = 一次 run；图是任意 DAG；`S<n>` 是稳定标识，转录按 `(feature_id, S<n>)` 幂等去重。没有可证伪验收项就拒绝切。**顺手补回 W3 放弃的一条代价** —— 图住在 spec 里，spec 的修订历史就是"我上次批准的是哪张图"。
- **W5 · 分支与 worktree 拓扑的不变量**（task #113）—— **机制早就在，缺的是填值的人**：arm 已收 `worktree:{branch,base}`，`base` 留空就落到主 checkout 的 HEAD，这正是 d153。定：slice 表加 `Base` 列 + 表头 `Base: <branch>`（人审 spec 时填，机器推不出来）；每 feature 一条 `f<N>/integration`；**每片跑完自己合回 integration**（否则 base 规则是空的，且十条攒到最后合是十倍冲突面）；链尾固定三片 `integrate → migrate+doctor → verify`，ship 不进表；「落零 commit 不许 done」**悬空** —— 原定落在 `verify_cmd`，但该字段是死的（**d158**：收集、存储、渲染，从不执行；109 次 run 中 `verify_exit_code` 为 NULL），改由 W11 一并回答谁来执行；**一个 schema 变更只能归一片**（S3/S5 撞 alembic 0029）。
- **W9 · 失败与重规划的状态机**（task #117）—— **不加状态。`done`/`failed` 两个出口够用，缺的是一条消息和一次落库。** DO-NOT-SHIP 与合并冲突都算 `failed`（理由进 `exit_reason`）；图停住等人**但必须发消息**（复用 `_announce_stuck`）；spec 图改了按状态三分（`idea` 更新 / 在跑的拒绝 / `done` 不动，只能追加新片）；defect 只记账不动图；数据库是事实、spec 的图是意图，分叉允许。**本票顺带声明了 d157**：失败片静默中断依赖链，零通知 —— 代码里真实存在的洞。
- **W11 · 每个项目的命令表：链尾三片跑什么**（task #126）—— **命令表是项目自己的文件 `.regin/commands.json`（与 `.regin/topics` 并排，进 git）**：`test`/`migrate`/`e2e` 三键必填，缺项显式 `null` 加理由（「没有迁移」与「忘了写」必须可分辨 —— d155 的成因）；探测生成草案、人确认后落盘（`repos.default_branch` 的先例）；表是默认值、每片 `verify_cmd` 是覆盖。**并顺带声明 d158 并定下：`verify_cmd` 要么让它真跑要么删掉** —— 109 次 run 里 `verify_exit_code` 一次都没写过，它被收集、存储、渲染，从不执行；本票选让它跑，实现另开。
- **W8 · build / verify prompt 由行生成**（task #116）—— **不写模板。**「模板」底下混着三样东西，各归各处：每片的 `body` 是从切片表**转录的内容**；脚手架 `<tracker>/<followup>/<feature>/<finish>` 已在 `_prompt` 的**代码**里；verify 的方法论归 `goal-verified` **skill**。build/verify **不使用 `prompt_templates` 表**（会变成第二个真相源）。定：片的 `body` 必须含 `Delivers` / spec 段落号 / **验收测试名（名字不是描述）** / 收尾动作，缺一即拒绝；verdict 必须落到 `task finish --status failed --reason`（该命令**已在**每条 prompt 里，缺的是绑定，且这一条**机器强制不了，记为已知缺口**）；**G-PROMPT 的"先记录后报告"要补给 native run，内容是"先合回 integration"** —— 与远程 run 的"先更新 issue"是同一条规则的两个实例。
- **W6 · 这条流水线怎么证明自己值得留**（task #114）—— **主判据是「每个 feature 需要的人类 prompt 数」，目标值 2（写 spec、批准图）。** rounds 与 escapes **不配当判据，它们不是差是空**：`restarted` 事件总共 2 条（75 个 feature 里 73 个 rounds 恒等于 1），可信 escape 只有 2 个，归属覆盖率 23/3154 = 0.7%。新判据零推断（是不是 task run 由 `agent_task_runs.trace_id` 的 join 判定），但要认一个方向明确的失真：**漏声明的恰好是人驱动的 session，漏算会让流水线显得更好看**。基线是 f73（需一次性考古补齐归属）；**三个 feature 之后判**；切分 / 转录 / 链尾三者可分开删。三条护栏：spec 字符数与验收项数一起报、批准恒计一次、成本与墙上时间一起报不折进主判据。**并声明 d159**：`docs/feature-pipeline.md:254` 指向的 `lib/experiments.py` 是 pattern 章节遮蔽实验，不是 A/B 框架 —— 整条流水线赖以被判定的测量背后没有框架。
- **W10 · 一个项目怎么导出它自己的闸门清单**（task #119）—— **闸门是三样东西叠起来的：通用失败模式（跨项目继承）+ 项目特有断言（探测/人写）+ 项目特有排序（只有语料能给，新项目留空、不假装有）。** W1 那 13 道里大部分是通用模式，regin 特有的是排序与断言 —— **模式照搬，断言重写，排序归零**。输入是 `missing_check` 的聚合，**语料自己会长**（`close()` 拒绝空值，37/37 已关闭缺陷都带着它）；导出可重复跑，**由计数触发而非时钟**；产物是一个 rule bundle（`checkers/` 放能机器化的，`SKILL.md` 放切分自检清单，其余**诚实记为缺口**）。meta-gate **每道闸门必须先见红**是通用的，列为第一条。

## Not yet specified

- **并发与配额**：同时在跑的 slice 上限、session 用完即结束的纪律（你手打过 `"end the session after the task finished, because now we have max current limit"`）。
- **planner 自身的账**：planner 是不是一个 run、它的成本记在哪个 feature 上、它失败算不算一轮。
- **spec 修订次数要不要进账本**（`docs/feature-pipeline.md` 说它是 rounds 之外的先行指标，今天没人记）。
- **plan 升成实体**（草案实体 / 批准状态机 / 版本化 / spec 变更后的失效规则 —— W2 的 B1、B2、B4）。**触发条件：第一次真的问出「我上次批准的是哪张图」时。** 在那之前它是 W3 明确拒绝的复杂度，不是遗漏。
- **批量 arm 的口子** —— `bulk_update` 的 `BULK_FIELDS` 只认 status/priority/project_id，所以"批准"今天是 N 次 `POST /arm`。触发条件：slice 数多到逐个 arm 变成负担。
- **这个仓库没有测试 CI**（`.github/workflows/` 只有 `deploy-website.yml`）。于是 `docs/feature-pipeline.md` 写的"验收项的第三份工作 —— ship 之后在 CI 里当回归哨兵"**今天根本没发生**：闸门只在有人跑链尾 `verify` 片时才生效。一道没人跑的闸门不是闸门 —— 但这不是本图任何一张票在解的问题。

## Out of scope

- 换 tracker、接 GitHub / GitLab —— feature 的 tracker 身份已经定死，不在这张图上重开。
- trace 子系统、memory 子系统本身的改动。
- **实现**。这张图只产出决策；走完后另开 feature 去建。
- **plan 的专属审批界面**（task #115，已关闭）—— W3 定了批准就是 arm，批准面就是已有的 `/tasks?feature=<ID>`，没有新界面要设计。
