## Question

**重新取景于 2026-09-16。** 原问题是「13 道闸门各自进流水线的哪一层」—— 问错了。那 13 道是从 **regin 自己的 43 个 defect** 里量出来的；regin 是个多项目 harness（8 个已注册仓库，`pattern_deployments` 29 条分布在 4 个项目上），换个项目那份清单和排序都不成立。原问法把一个实例当成了交付物。

正确的问题是：**一个项目怎么导出它自己的闸门清单？**

- **输入**是什么？regin 对每个注册仓库都有 defect 表 —— 但一个新项目的语料是空的。冷启动怎么办：从零、还是有一份跨项目的通用底座？
- **导出**是一次人工审计，还是可重复跑的东西？多久重跑一次，触发条件是什么（W1 那次是一个 research subagent 读全表 + 程序验证划分不重不漏）。
- **输出的形状**是什么？至少要能表达：闸门的断言、它挡掉过几个、它落在哪一层。
- **层**有哪几种？候选：常驻闸门（写死一次，全仓库永久）· 每 feature 验收项（planner 每次生成）· 规则引擎（每次编辑就跑）· 诚实记为缺口。**注意这个仓库没有测试 CI**（`.github/workflows/` 只有 `deploy-website.yml`），所以"常驻"只在有人跑链尾 `verify` 片时才生效。
- **怎么投递到别的项目？** 机制已经有了 —— pattern bundle + rule engine 按项目部署（`pattern_deployments`，29 条 / 4 个项目）。导出的清单要不要落成一个 bundle？
- **meta-gate**：每道闸门必须先见红才算数（W1 从语料里挖出来的 —— d128/d134/d135 自己记录了"revert 掉修复后测试变红"）。这一条是通用的还是 regin 的偏好？

必用 `/grilling` 和 `/domain-modeling`。

---

## regin 的那份清单 —— 当样本，不当结论

W1 的完整闸门表在 `research/w1-defect-root-causes.md`（分支 `research/w1-defect-root-causes`）。13 道恰好划分全部 43 个 defect：

| 闸门 | 挡 | 一句话 |
|---|---|---|
| G-RESP | 8 | 全宽度响应式扫描：fixed 元素 rect 两两求交、不被窗格裁掉、每个断点可达 |
| G-STATE | 7 | 状态机守卫：枚举成员都有入边、无不可恢复态、生命周期写不到 claim 列、删除要么级联要么拒绝、启动结算未决行 |
| G-SURFACE | 5 | 每个**真正执行 X 的界面**一条验收项，而非 spec 点名的那个 |
| G-SERIALIZER | 4 | 断言真实序列化器，永不断言桩；API 行必须带齐 UI 读的每一列 |
| G-MODELSHAPE | 3 | 一个形状一种拼法；界是算出来的不是字面量 |
| G-REGISTRY | 3 | 注册表完备性：每个注册的 X 都有它的 Y，没有两项渲染相同 |
| G-REALDATA | 3 | 对抗性 fixture：同一 id 出现在两条 trace、两种时间戳拼法、补白/空键、声明的上限真的被强制 |
| G-BASE | 2 | 集成基座拓扑（**已由 W5 落位到 `verify_cmd`**） |
| G-MIGRATE | 2 | schema 漂移（**已由 W5 落位到链尾 `migrate` 片**；闸门本身已建好） |
| G-WHOLESUITE | 2 | 任何 done-claim 前跑全量 `tests/`（**已由 W5 落位到链尾 `verify` 片** —— 因为没有 CI，那是唯一能跑它的地方） |
| G-DUPRENDER | 2 | 一个 span 的正文被复制到另一张卡上时，断言源行不再渲染它 |
| G-INFLIGHT | 1 | 挂起响应：一行在拿到答案前不断言任何存活性 |
| G-PROMPT | 1 | prompt 契约：agent 的 finish block 把 tracker 更新写成**前置条件**（**已由 W9 交给 W8 的模板契约**） |

三道已由 W4/W5 落位（G-SURFACE→切分自检、G-BASE→`verify_cmd`、G-MIGRATE→链尾），一道交给 W8（G-PROMPT）。**这四次落位本身就是那个导出方法的四个样本** —— 本票要做的是把它们背后的规则说出来。