## 问题

一个 task 跑完后，人在 Task 页面加了 comment，再重跑，comment 不会进 prompt。

不是 bug，是缺口：`lib/tasks/runs.py::_prompt()` 只拼 `_invocation` + body/title + `<tracker>` + `<feature>` + `<finish>`，从不读 `comment_threads`。regin 自己的 comment 表今天只有两个消费方——`web/blueprints/tasks.py:730-795` 的三个 API 和 `frontend/src/components/tasks/TaskComments.vue`——纯人对人。

于是「跑完才发现还要补一句」没有出路：只能覆盖 body（就地覆盖、无历史，原始需求消失），或者重开一个 task。

## 决策

未 resolved 的 comment thread 进这个 task 下一次 run 的 prompt，每次 run 都带，直到有人 resolve 它。

**不新增表、不新增类型、不新增 UI。** 复用 `comment_threads.resolution_state`——它的注释已经把这根轴定义成 *"answers only has this conversation been dealt with"*，而「还没被处理的对话」和「agent 下次该看的东西」本来就是同一件事。再加一个「要不要投递」的勾选框，就是第二根说同样事情的轴。

代价是纯闲聊的 comment 也会进 prompt；想避免就 resolve 它。这反过来让 resolve 这个动作第一次有了实际后果——今天 resolve 一个 thread 不产生任何效果。

### 明确排除

- **不做实时投递**。不往正在跑的 session 推 comment（不碰 `bridge_messages` / steer）。想干预正在跑的 session，直接打开那个 session 自己敲。
- **不做 amendment 类型**。曾考虑在 comment 之外再立一类「补充指令」，在只剩一个投递时刻之后它和 comment 没有可区分的语义。
- **不接 agent 写入**。`comment_threads.author_kind` 里预留的 `'agent'` 保持未接通。agent 已经有 `regin defects open` 表达「我发现了问题」，再给一条写 comment 的路会变成同一件事两个出口。

## 行为

在 `lib/tasks/runs.py` 新增 `_followup_block(task_id) -> str`，产出 `<followup>` 块。

**位置**：`_tracker_block(rows)` 之后、`_feature_block(...)` 之前。在 `PROMPT_MAX` 截断**之外**拼接，和其它三个块一致——截断是「被捕获的那个想法」的属性，不该吃掉后续指令。

对 linked task 而言，`<tracker>` 先声明外部 issue 是 source of truth，`<followup>` 随后作为「另外，本地还留了这些话」出现，顺序不能反。

**取数**：`comments.thread_with_comments("task", str(task_id))`，只保留 `resolution_state == "open"` 的 thread。

**只取这个 run 自己所属 task 的 thread**，不含父 task 的。父 task 的 comment 是对 feature 说的，子 task 的 run 是一次具体的活；串上去会让每一轮都背着整个 feature 的历史讨论。

**顺序**：thread 按 `created_at` 升序，thread 内 comment 按 `created_at` 升序。追加指令是后来的修正前面的，时间序是唯一正确的序。

**渲染**：每条 comment 标出 `author_kind`。块的开头一句话说清它是什么、以及它为什么还在这里（未 resolved），避免 agent 把它当成已经处理过的历史。

**上限**：新常量 `FOLLOWUP_MAX`（与 `PROMPT_MAX` 并列）。单条 comment 上限已是 8000 字符（`COMMENT_BODY_MAX`），thread 数量无上限，不设界会挤爆 prompt。超出时**保留最新的**并在块内声明丢弃了几条——后来的修正前面的，所以丢旧不丢新。

**空则不出现**：没有 open thread 时返回 `""`，不是空块，和 `_tracker_block` 同构。此时 prompt 必须与今天逐字相同。

## 验收项（每条一个测试）

1. task 有 1 条 open thread → `_prompt()` 含 `<followup>`，且含该 comment 正文与 author_kind
2. thread 被 resolve → 不出现在 `<followup>` 中
3. 没有 open thread → `_prompt()` 的输出与本改动前逐字相同
4. 同一条 open thread 在连续两次 run 的 prompt 中都出现（累积，不因投递而消耗）
5. 总长超过 `FOLLOWUP_MAX` → 保留最新的若干条，块内声明丢弃数量
6. 父 task 上的 comment 不出现在子 task 的 run prompt 中
7. 块顺序：`<tracker>` 在 `<followup>` 之前，`<followup>` 在 `<feature>` 之前