# regin MCP 功能支持完善

## 意图

第三方 agent（CodeBuddy、WorkBuddy、任何只会说 MCP 的 agent）只靠 `regin` MCP server 就能走完日常的 pipeline 开发：建 feature、写 spec（带图）、切 task、挂图、关联 task / feature / defect、报 defect 并修掉、汇报 run 结束、宣告 ship。今天 server（`lib/regin_mcp/server.py`）只有 11 个工具，覆盖"建、读、列"，Web UI 上大部分写操作（改、关联、挂图、生命周期动作）都没有。

**非目标**
- 不从 MCP 启动 / 武装 regin 自己的 run（`arm` / `run` / `stop` / `interrupt` / `arm-slices`）。第三方 agent 自己就是干活的那个，替它再拉起一个 Claude/Kimi 是另一回事，得单独做信任决策 —— 列为后续。
- 不碰 tracker 同步（`refresh`、adopt 远端 issue）、看板 / 计数 / 图谱这类纯展示的读，也不做 project stage 配置。
- 不改 defect 的图片语义：defect 的图是证据缩略图条（`DefectImages.vue`），symptom 里不放 `![](image:N)` 引用。
- 不加新的 HTTP 端点。MCP 是进程内调用 store 层，不走 `/api` —— 唯一例外是 `task_finish`：它和 `regin task finish` 共用同一条投递路径（`lib/tasks/finish_report.py`），先发到运行中 server 已有的 loopback 端点 `/api/tasks/runs/<id>/finish`，连接被拒才在本进程结算。这样它释放的依赖任务由常驻 server 拉起，SDK 子进程才不会随 stdio 进程一起死掉。

## 现状 → 缺口

| UI 能力 | REST 端点 | MCP 今天 | 本 feature |
|---|---|---|---|
| feature 建 / 读 / 列 / 改 spec | `POST/GET /api/features`, `PATCH` | ✅ create/get/list/set_spec | `feature_get` 补 `images`、`links` |
| feature 改标题 / project / 分支 | `PATCH /api/features/<id>` | ❌ | `feature_update` |
| feature ship / reopen / abandon | `POST …/ship\|reopen\|abandon` | ❌ | `feature_lifecycle` |
| feature ↔ feature 关联 | `POST/DELETE …/links` | ❌ | `feature_link` |
| session 归属 feature | `POST/DELETE …/attribute` | ❌ | `feature_attribute` |
| 开某 stage 的 step task | `POST …/steps` | ❌ | `feature_open_step` |
| spec 切片表 → task | `GET …/slices`, `POST …/transcribe` | ❌ | `feature_slices`, `feature_transcribe` |
| task 建 / 读 / 列 | `POST/GET /api/tasks` | ✅（list 只能按 status 筛） | `task_list` 加 `feature_id` / `project` / `query`（走 web 同一套 `query.parse_filters` / `apply_filters`；`status=""` 列所有状态含 archived）；`task_get` 补 `images`、`deps`、`comments` |
| task 改文本 / 状态 / 优先级 / 改挂 feature | `PATCH /api/tasks/<id>` | ❌ | `task_update` |
| task 依赖 | `PUT …/deps` | ❌ | `task_set_deps` |
| task 评论 | `GET/POST …/comments`, `PATCH …/<thread>` | ❌ | `task_comment` |
| run 汇报完成 | `POST /api/tasks/runs/<id>/finish` | ❌（只有 CLI） | `task_finish` |
| defect 开 / 列 / 关 | `POST/GET /api/defects`, `…/close` | ✅ | `defect_list` 加 `status`；新增 `defect_get` |
| defect 改文案 / 嫌疑路径 | `PATCH /api/defects/<id>` | ❌ | `defect_update` |
| defect 归因到 feature | `POST …/attribute` | ❌ | `defect_attribute` |
| defect ↔ 修复 task | `POST/DELETE …/fix-item`, `POST /api/defects/fix-task` | ❌ | `defect_set_fix_task` |
| defect 分诊 | `…/reopen\|decline\|restore\|snooze\|unsnooze\|merge` | ❌ | `defect_triage` |
| 图片 上传 / 删除 / 查看（三种 owner） | `POST/DELETE/GET …/images[/<idx>]` | ❌ | `image_attach`, `image_remove`, `image_get` |

## 形状

```ts
type Owner = "feature" | "task" | "defect"
type ImageMeta = { idx: number; media_type: string; byte_size: number }

image_attach(owner: Owner, owner_id: int,
             path?: string, data_b64?: string,      // 恰好给一个
             idx?: int,                              // 提议，不是要求（同 wanted_idx）
             place?: "append" | "none" = "append")
  → ImageMeta & { ref: string | null,                // "![](image:N)"；defect 为 null
                  placed: boolean,                   // 本次是否往 body 追加了 ref
                  requested_idx: int | null }        // idx 被占时 ≠ 返回的 idx
image_remove(owner, owner_id, idx) → { removed: true, idx }
image_get(owner, owner_id, idx)    → [MCP ImageContent, { idx, media_type, byte_size, path }]
                                     // 同时落盘到 <tmp>/regin-<owner>-images/<id>-<idx>.<ext>（`attached_images.write_file`，`regin feature image` 共用）

feature_create / task_create / defect_open 增加 image_paths?: string[]
    // 先把每个文件读出并按 `check_usable` 校验，全部合格才建行；建行后逐个 attach(place="append")

feature_update(feature_id, title?, project?, branch?)          // 省略=不动；project="" 取消归属，branch="" 清空
feature_lifecycle(feature_id, action: "ship"|"reopen"|"abandon", session_id?)
feature_link(feature_id, other_id, kind: "follows"|"related", remove?: bool) → { links }
feature_attribute(feature_id, session_id, remove?: bool, primary?: bool)   // provenance 恒为 recalled，同 web
feature_open_step(feature_id, stage: string, body?) → { task, created }
feature_slices(feature_id) → SlicesReport
feature_transcribe(feature_id) → 落成的 idea task + 依赖边（不武装）

task_update(task_id, title?, body?, status?: "idea"|"archived", priority?, kind?, feature_id?, project?)
    // 省略=不动；feature_id=0 摘离 feature，project="" 取消归属
task_set_deps(task_id, blockers: int[])
task_comment(task_id, body, thread_id?, resolve?: bool)   // 作者 author_kind=agent；给了 thread_id+resolve 时 body 可空
task_finish(run_id, status: "done"|"failed", reason, task_id?)

defect_get(defect_id)
defect_update(defect_id, symptom?, paths?)
defect_attribute(defect_id, feature_id)
defect_set_fix_task(defect_ids: int[], task_id?: int, create?: bool, remove?: bool)
defect_triage(defect_id, action: "reopen"|"decline"|"restore"|"snooze"|"unsnooze"|"merge", into_id?, reason?)
    // reason 只用于 decline
```

`server.py` 按实体拆成 `lib/regin_mcp/{features,tasks,defects,images}.py`，外加 `pipeline.py`（`pipeline_overview`）和共享查找的 `_common.py`；`server.py` 只负责组装工具表。defect 的所有输出（open/get/list/close/…）都是 `lib/defects/view.py` 的 `serialize` / `view`，与 web 同形。

## 规则

### 图片占位（`place`）

| owner | `place` | body 已含 `![](image:N)`（N = 实际 idx） | 结果 |
|---|---|---|---|
| feature / task | append | 是 | 不改 body，`placed=false` |
| feature / task | append | 否 | body 末尾追加 `\n![](image:N)\n`，`placed=true` |
| feature / task | none | — | 不改 body，`placed=false`，`ref` 照常返回 |
| defect | 任意 | — | 无 body 引用，`ref=null`，`placed=false`；图出现在缩略图条 |

task 的 body 经 `capture.parse_update` 写入，首尾空白会被规整，所以 task 上追加后末尾的 `\n` 不保留。追加会让 body 超长时，刚挂上的图会被撤掉，并报 ToolError（field `body`）。

| 请求的 `idx` | 状态 | 实际 idx |
|---|---|---|
| 未给 | — | high-water + 1 |
| N | 空闲且 ≤ high-water 从未发出过 | N |
| N | 已被占用，或曾发出后被删 | high-water + 1，且 `requested_idx=N` ≠ `idx` |

所以"先写 spec 里放 `![](image:1)`，再 `image_attach(idx=1)`"的作曲流程和 UI 粘贴一致；落不到 N 时返回值说明白，由 agent 自己改 body。

### 图片输入

| 输入 | 结果 |
|---|---|
| 同时给 `path` 和 `data_b64`，或都不给 | ToolError，指明字段 |
| `path` 不存在 / 不可读 | ToolError，指明路径 |
| 字节不是 png/jpeg/gif/webp | ToolError `not a supported image …`（`sniff_media_type`） |
| 超过 `prompt_image_max_bytes` / 已有 `prompt_images_max_count` 张 | ToolError，原样转述 `ImageSet` 的理由 |

### 生命周期签名

| 工具 | `declared_by` | `trace_id` |
|---|---|---|
| `feature_lifecycle` | `operator` —— 第三方 agent 是在人的直接指令下动手，这是人的动作 | 传入的 `session_id`（可空） |
| `feature_create` | 不变 | `session_id` |
| `task_finish` | 与 `regin task finish` 同一路径（`finish_report.report_finish`）：先投 loopback 端点，连接被拒才在本进程 `runs.finish_run(in_server=False)`；有未关 defect 时的 409 → ToolError 带 `open_defects` | — |

### 不变式

- **一条规则一处实现**：MCP 工具与对应 blueprint 调同一个 store / 校验函数（如 `capture.parse_update`、`deps.set_deps`、`links.link`、`defect_store.*`、`ImageSet`）。同样的输入从 MCP 和 `/api` 进来，落出的行一字不差。原本只活在 blueprint 里的逻辑已经下沉到 lib：`lib/defects/view.py`（defect JSON）、`lib/image_payload.py`（base64 解码 / idx 提议）、`attribution.require_known_trace` / `no_declaration`、`steps.open_step` 内的 body 上限、`comments.thread_for`、`attached_images.check_usable` / `write_file`、`finish_report.report_finish`（CLI 与 MCP 共用）。
- **MCP 进程不拉起 SDK 子进程**：`task_set_deps` 释放已就绪任务时调 `runs.fire_if_ready(in_server=False)`。tmux 任务立即启动；SDK 任务标为 deferred，等 server 下一次 fire 或重启时拉起（与 CLI 无 server 时的 D5 规则一致）。这是已知的延迟，并非丢失。
- **拒绝是 ToolError，不是静默**：每个 store 拒绝（`TransitionRejected`、`VerbRefused`、`LinkRefused`、`StepRejected`、依赖成环、未知 id、未知 project）都变成一条点名字段 / 原因的 ToolError。
- **列表永远包在对象里**（沿用 server 里已有的约定），空结果对只读 content 的客户端也是"0 行"而不是"什么都没有"。
- **读不带字节**：`feature_get` / `task_get` / `defect_get` 里的 `images` 只有 `ImageMeta`；字节只走 `image_get`。
- `--no-send-to-user` 注册的 server 也带上全部新工具。

## 验收

```gherkin
Feature: 第三方 agent 只用 regin MCP 跑通 pipeline

  Scenario: feature spec 带图并占位
    Given 一个 feature，spec 为 "见图"
    When 调 image_attach(owner="feature", owner_id=F, path=<一张 png>)
    Then 返回 idx=1、ref="![](image:1)"、placed=true
    And feature_get(F).spec 以 "![](image:1)" 结尾
    And feature_get(F).images == [{idx:1, media_type:"image/png", …}]
    And GET /api/features/F/images/1 返回同样的字节

  Scenario: 先写引用再上传，idx 对齐
    Given task_create(body="步骤\n![](image:1)\n")
    When image_attach(owner="task", owner_id=T, data_b64=<png>, idx=1)
    Then 返回 idx=1、placed=false，task body 不变

  Scenario: 被占用的 idx 会如实报告
    Given task T 已有 image 1
    When image_attach(owner="task", owner_id=T, path=<png>, idx=1, place="none")
    Then 返回 idx=2、requested_idx=1，body 不变

  Scenario: defect 截图进缩略图条、不进 symptom
    When defect_open(symptom="按钮重叠", image_paths=[<png>])
    Then defect_get(D).images 有 1 张，symptom 仍为 "按钮重叠"

  Scenario: 非图片被拒绝
    When image_attach(owner="feature", owner_id=F, path=<一个 .txt>)
    Then ToolError 含 "not a supported image"，feature_get(F).images 为空

  Scenario: 关联 task / feature / defect
    Given feature F、task T（未挂 feature）、defect D（未归因）
    When task_update(T, feature_id=F)
    And defect_attribute(D, F)
    And defect_set_fix_task([D], task_id=T)
    Then feature_get(F).tasks 含 T，feature_get(F).defects 含 D
    And defect_get(D).fix_task_id == T
    And defect_list() 不再列出 D（已不在待归因队列）

  Scenario: 依赖成环被拒
    Given task_set_deps(A, [B])
    When task_set_deps(B, [A])
    Then ToolError 点名成环的那一对，B 的依赖不变

  Scenario: spec 切片落成 task
    Given feature F 的 spec 含合法切片表
    When feature_transcribe(F)
    Then feature_get(F).tasks 是表里的每一行，状态都是 idea，没有一个被武装

  Scenario: ship 与重开
    Given feature F 处于 spec 或 building
    When feature_lifecycle(F, "ship", session_id=S)
    Then feature_get(F).stage == "shipped"，该事件 declared_by="operator"、trace_id=S
    When feature_lifecycle(F, "ship")
    Then ToolError "#F is already shipped"

  Scenario: run 通过 MCP 汇报结束
    Given task T 有一个进行中的 run R
    When task_finish(R, status="done", reason="…")
    Then 结果与 `regin task finish T --run R` 相同
    And server 在跑时，报告经 `/api/tasks/runs/R/finish` 投递；不在时才在本进程结算

  Scenario: 坏的 image_paths 不留半截行
    When feature_create / task_create / defect_open 的 image_paths 含不可读或非图片文件
    Then ToolError 点名该路径，且没有新建任何行

  Scenario: MCP 与 Web 同源
    Given 同一份 task 编辑分别经 task_update 与 PATCH /api/tasks/<id> 写入两个 task
    Then 两行除 id / 时间戳外字段相同

  Scenario: 工具清单
    When 以 --no-send-to-user 通过 stdio 启动 server 并 list_tools
    Then 列出本 spec「形状」里的每个工具，且没有 send_to_user
```

上面的 Gherkin 都落在 `tests/regin_mcp/` 里（按工具模块拆文件），走真实 store 和 DB fixture，不 mock store。


