## Question

图跑起来之后总会有东西坏掉。今天的处理是人手开一个新 task（`"Fix defects 152, 153, 154"`）再手动挂边。

- 一个 slice **失败**（`task finish --status failed`）时，它的 dependent 怎么办 —— 永久阻塞、跳过、还是整张图暂停？今天 autorun 级联只在 `done` 上触发，失败就是静默停住。
- verifier 判 **DO-NOT-SHIP** 时呢？那不是 failed，是「建完了但不达标」。这算不算一个 task 状态？
- **重规划**：planner 能不能在图跑到一半时被重新调用？它看到已 done 的 slice 怎么办？
- 执行中冒出来的 **defect** 怎么进图 —— 自动生成 fix task 挂在受影响 slice 之后，还是只记账不动图？
- 人**中途插手**（手动改边、手动 arm）之后，plan 与实际的图就分叉了。谁是事实来源？

产出一张状态机。必用 `/grilling` 和 `/domain-modeling`。

## W4 交过来的一问（2026-09-16）

W4 定了转录按 `(feature_id, S<n>)` 幂等去重。但它只答了"已存在就更新"，没答**已经 armed / running / done 的那片，spec 的图又改了怎么办** —— 更新它的 body 是在改一个正在跑的东西，跳过它则图与 spec 不符。这一问归本票。

## W5 交过来的一问（2026-09-16）

W5 定了「每片跑完自己合回 integration」。**合不进去怎么办** —— 冲突、或者上游片把下游片依赖的假设改掉了？这一片算 failed 吗？它的 dependent 怎么办？归本票。