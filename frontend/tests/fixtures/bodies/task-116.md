## Question

今天每条 slice 的 build prompt 和 verify prompt 都是人手写的散文（S1–S9 各一段，外加十段 `"You did NOT write this code…"`）。要把它变成从 plan 行拼出来的东西。

- 一条 build prompt 的**模板契约**是什么：spec 的哪一段、哪些验收测试名、worktree 路径、解释器路径、skill 名（今天手打 `/goal-verified-treenav`）、完成后的 `task finish` 指令？
- verify prompt 同理 —— 「gate owner: yes/no」「不要重跑 pytest，复用这段输出」这些今天靠人记的东西，哪些能从行里读出来？
- 模板存在哪：`prompt_templates` 表已经存在，用它还是新开？
- 哪些字段**必须**人写（没有它 prompt 就是废的），哪些能推导？
- 散文里那些「不要 cd 到主仓库」之类的纪律，是进模板，还是进 skill，还是进 CLAUDE.md？判据是哪一层能被机器强制。

必用 `/grilling` 和 `/domain-modeling`。

---

## W2 落下来的修正（2026-09-16）：这张 ticket 比想的小

`lib/tasks/runs.py:524` 的 `_prompt` **已经在组装**启动提示词：skill 调用行（`_invocation`）+ body + `<tracker>` + `<followup>` + `<feature>` + `<finish>`。已核对源码属实。

所以 planner 欠的不是「每片一段散文」，而是 `title` / `body` / `depends_on` / arm 参数（含 `skill` 名）。本 ticket 要回答的收窄成两问：

1. **一片的 `body` 里该有什么** —— f73 那些手写散文里，哪些其实是 `_prompt` 已经供的（worktree 路径、解释器、完成指令），哪些是真正的每片内容（spec 段落、验收测试名）？
2. **verify 那一半在哪** —— 那十段 `"You did NOT write this code…"` 不是 regin 生成的，是 `goal-verified` 这个 skill 自己派 subagent 时写的。所以问题变成：verify prompt 的契约归 regin 的模板，还是归 skill？判据还是那个 —— 哪一层能被机器强制。

## W4 落下来的输入（2026-09-16）

每片 `body` 的来源已经定了一半：**slice 表的 `Delivers` 列 + 该片引用的 Rules / Acceptance 段落**。本票剩下的是把它写成模板契约，以及回答 verify 那一半归 regin 还是归 skill。

## W5 落下来的输入（2026-09-16）

每片的 arm 参数已经定了三项，本票只需把它们写进模板契约：
- `worktree: {branch: "f<N>/s<n>", base: <slice 表 Base 列>}`
- `verify_cmd`：`git rev-list --count <base>..HEAD` 大于零 + 断言 integration 含本 feature 主体路径
- 片的最后一个动作是**合回 integration** —— 这句归 body 还是归 skill，是本票要答的"哪一层能被机器强制"。

## W9 落下来的一句（2026-09-16）

verifier 的结论今天只写在会话对话里，数据层一无所知。**verify prompt 必须以 `task finish --status failed --reason "<why>"` 收尾**（判 DO-NOT-SHIP 时），这是本票模板契约里不可省的一行。

## W11 落下来的输入（2026-09-16）

命令从 **`.regin/commands.json`** 读（`test` / `migrate` / `e2e`，缺项显式 `null`）。转录时每片的 `verify_cmd` **默认填表里的 `test`**，slice 表的行可覆盖。本票的模板契约要写清楚：prompt 里出现的每一条命令都从这张表来，**没有一条是模板写死的** —— 写死就只对 regin 成立。