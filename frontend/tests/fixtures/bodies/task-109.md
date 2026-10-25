## Question

读 `defects` 表全部 43 行（含已 closed 的 `missing_check` 字段）以及它们关联的 session，回答：

1. 每个 defect 属于哪一类 —— **流程/集成性**（分支拓扑、迁移、worktree base、协调失误）、**代码逻辑性**、**UI/视觉性**、**规格性**（spec 本来就没说清）？给出分类表与计数。
2. 按「如果有这道闸门，能挡掉几个」排序，产出**缺失闸门清单**。`missing_check` 字段里已经有人写过答案，先聚合它们。
3. 哪些 defect 是在 ship **之后**才冒出来的（escape），哪些是流水线自己抓到的？两者的类型分布有没有差别？

产出按 `docs/spec-notation.md` 的决策表写。不要修任何代码。