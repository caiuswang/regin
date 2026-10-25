## Question

这是整张图的承重决策，其它多数 ticket 挂在它下面。

- 一个 **plan 草案**由什么构成？至少要能表达：slice 列表、每个 slice 的 spec 依据（验收项/段落）、依赖边、base 分支与 worktree、verify 命令、arm 参数、执行用的 skill。写成 `docs/spec-notation.md` 的类型定义。
- 它**存在哪**：新表 `feature_plans`（+ `plan_slices`），还是复用 `agent_tasks` 加几列？判据是这些字段是否在 task 生命周期之外还有意义。
- plan 要不要**版本化**？重规划是改这一版还是开新一版？（`features` 的 append-only 事件日志是现成的先例。）
- **批准**这个动作在数据上写什么？批准前 slice 以什么形态存在 —— 已经是 `idea` 态的 task，还是根本还不是 task？（capture invariant：捕获的 task 是惰性的，arm 是特权步骤。）
- 一个 plan 与一个 feature 是 1:1 吗？

必用 `/grilling` 和 `/domain-modeling`。

---

## W2 落下来的输入（2026-09-16，task #110）

全文见 `research/w2-planner-prior-art.md`（分支 `research/w2-planner-prior-art`）。带进这张 ticket 的是：

**别再问「有没有现成的」—— 已经问过了。** 九个系统里没有一个把「待批准、未接受的 plan」存成实体。所以 B1–B4 是我们自己的活：草案实体、批准/驳回状态机、**同时吐节点与边**的 planner、批准后 `features.body` 又改时的失效规则。

**可以直接对着抄的六个形状**（挑，不是全要）：

| 形状 | 它解决本 ticket 的哪一问 |
|---|---|
| GitHub `DraftIssue`：提案时铸 id、转正后 id 存活 | 「批准前 slice 以什么形态存在」—— 有 id，但还不是 task |
| Linear `issueBatchCreate` + 客户端 UUID | 「批准在数据上写什么」—— 一次原子事务 |
| Airflow `hitl_detail`：一行 = 请求半 + 响应半，`responded_at IS NULL` 即待批 | 同上，且不用单开状态列 |
| Prefect `RunInput`：发布 JSON Schema、服务端校验、`(entity_id,key)` UNIQUE | 批准表单的契约 |
| Dagster：graph / plan 两级 + 内容哈希 | 「要不要版本化」—— 内容哈希回答「还是同一张图吗」 |
| Airflow：**逐节点**版本钉住 | 「重规划改这版还是开新版」 |

**两条可能让这张 ticket 变小的观察**：
1. **批准 ≈ arming。** capture invariant 已经给了惰性草案态（捕获即 `idea`，arm 是特权步骤）。先问 B2 是不是只需要给现有状态机命名，而不是造新的。
2. regin 用**显式边表**，四个引擎都把邻接存在节点上。这个分歧站得住，但**要在这里被显式记成一条决策**，别当默认。