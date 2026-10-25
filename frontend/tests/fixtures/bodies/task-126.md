## Question

**W5 的遗漏，由可移植性这一问暴露出来。**

W5 定了链尾固定三片 `integrate → migrate+doctor → verify`，但那三条命令是 **regin 特有**的：`regin doctor` 是 alembic，`verify` 是全量 pytest。换个 TypeScript 项目，两条都不存在。

今天**没有任何地方**存"这个项目怎么跑测试"：`repos` 表只有 `id` / `name` / `path`；`verify_cmd` 是每个 task 自己的，由写 prompt 的人当场填。`docs/cross-project-portability-audit-2026-09-02.md` 当时的修法是把 agent 里写死的 `pytest` 改成散文 **"your repo's test runner"** —— 也就是让 agent 每次自己猜。

**猜就是 d155 那种 bug 的制造机**：那次正是没人想起来要迁库。

要回答：

- **每个项目存一份命令表**吗？至少要哪几条 —— migrate / test / browser-verify / lint / build？
- **存在哪**：`repos` 加几列、一张新表、还是一个项目内的文件（像 `.regin/` 下的一份配置）？判据是"谁改它、多久改一次"。
- **缺项怎么办**：一个项目没有 migrate 这一条，链尾就少一片，还是那一片变成显式的 no-op？**少一片和忘了一片必须能分辨** —— 这正是 d155。
- **谁填**：注册仓库时问、第一次跑链尾时问、还是 agent 探测（看到 `pyproject.toml` 就猜 pytest）？探测要不要落库成一条可覆盖的默认值，而不是每次重猜？
- 这份表和已有的 `verify_cmd`（每 task 一条）是什么关系 —— 默认值与覆盖？

必用 `/grilling` 和 `/domain-modeling`。

## 背景事实

- 已注册仓库 8 个：regin · regin-skillhub · note · oh-my-pi · aural-kids · computer · writing-coach · deepseek-harness。至少两个是 TypeScript。
- `pattern_deployments` 29 条，分布在 4 个 `project_id` 上 —— 按项目投递的机制已经存在。
- 可移植性审计的结论：**基质是真正跨项目的，投递和 skill/agent 的内容不是。**