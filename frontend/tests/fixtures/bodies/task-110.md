## Question

在自造 planner 之前，先看别人怎么做的（站着的偏好：先查成熟方案再自造）。调研并回答：

1. **agent 编排类**：Claude Code 自己的 Plan mode / Task 工具、OpenHands、Devin 的 planning、Aider 的 architect mode —— 它们怎么把一个需求切成可并行的单元？切片粒度由什么决定？有没有「人批准后才执行」这一步，长什么样？
2. **DAG 授权 UX**：Temporal、Airflow、Dagster、Prefect —— 一个**待批准的 DAG 草案**在这些系统里怎么表达（是一等实体，还是只是代码）？版本化怎么做？
3. **issue → 子任务**：Linear 的 agent sessions / sub-issue 自动拆分、GitHub Projects —— 有没有可直接借用的数据模型。

对每一条明确回答：**能直接借用 / 只能借思路 / 必须自造**，并说明为什么。产出写成 Markdown 落在 `research/` 分支上，ticket 里只留指针。