## Question

defect 152、153、155 是**同一类**：拓扑没被建模，所以每次由人重新推导，推错了就是 bug。

- 152：slice 的 worktree 从裸 master 切，拿不到前序 slice 的成果。
- 153/152：`f73/integration` 从 master 切，里面一个 slice 都没有。
- 155：合并落地后没人迁库，live DB 落后 alembic head，`/api/features` 整个 500。

要回答：

- 一个 slice 的 **base** 由什么决定？（它依赖的 slice 的产出？integration 分支的当前头？）谁写这个字段，谁读。
- **integration 分支**是不是一等概念？它从哪切、什么时候切、谁往里合。
- 链尾是不是**固定**的 `integrate → migrate → browser-verify → ship`？如果是，它由 planner 生成还是模板写死？
- 这些要成为**结构不变量**（写入即拒绝违例），还是只是 planner 的默认值？判据：一条只靠提醒维持的规则挡不住 152。
- 多个 feature 同时在跑时，worktree 目录与分支名怎么不撞。

产出至少一组 invariant 和一张状态机。必用 `/grilling` 和 `/domain-modeling`。

---

## W1 落下来的修正（2026-09-16）

**前提要改**：W1 量过了 —— 流程/集成类缺陷**从不逃逸**（8 个 naive escape 里 0 个）。它在制造它的那一轮里就炸。所以这张 ticket 的收益是**缩短轮数**，不是提升 ship 质量；别把它当逃逸预防来论证。相关闸门 G-BASE(2)、G-MIGRATE(2)、G-WHOLESUITE(2) 的分量也就这么大。

**一条实拍证据**：charting 当天，两个 research subagent 与一个 live run（task #118，`cwd=/Users/taowang/regin`、`worktree=null`）共用主 checkout；subagent 的 `git checkout -b` 把分支从 master 切走，live run 的编辑差点落到研究分支上。没有任何地方声明过「这个 run 独占主 checkout」。把这个场景当作本 ticket 的验收用例之一。

---

## W3 落下来的重新取景（2026-09-16）

W3 决定不造 plan 实体、不建新表，所以**拓扑不再有「进数据模型」这个选项**。本票的问题换成：

- `/plan-feature` 该给每片的 **arm 参数**里写什么 `worktree` / `cwd`，`base` 从哪来？（`ARM_FIELDS` 已有 `worktree`，见 `lib/tasks/arm.py:25`）
- **链尾（integrate → migrate → verify）是不是固定**由 planner 每次生成的几片？如果是，它是 skill prompt 里的模板，不是表里的约束。
- 既然只能靠 prompt 约束，那就要诚实回答：**这一层挡不住的，就记为已知缺口**（W1 已量过：流程/集成类只占 6/43，且从不逃逸）。

## W4 落下来的一问（2026-09-16）

W4 定了：切分写在 spec 的 slice 表里，转录是机械的。所以本票多一问：**链尾（integrate → migrate → browser-verify → ship）是不是第一段 skill 每次固定写进那张表的几片？** 如果是，它是 spec 模板的一部分，不是数据层的约束 —— 也就意味着它可以被人在审 spec 时删掉，这个风险要认。f73 的修复链（`f73-fix A→B→C→D→E`）就是事后手工补的这条尾巴。