# F134 Claude Code Mod 调研

## Intent
Claude Code 2.1.289 的 Mod 是一种函数钩子插件：进程内运行的 TS 模块，`register(on)`，每个钩子的签名是 `($, e, next)`（官方的 diff 视图就是用它做的）。本 feature 的目标是判断它能替代 regin 里的哪些机制、能新增什么，以及哪些地方不该用它。

## Non-goals
- 不迁移 Codex/Kimi provider：Mod 只支持 Claude Code，`hook_manager` 要保留，作为跨 provider 的统一入口。
- 本轮不替换 turn_trace 的 usage 回填（映射表第 2 行，P1）：trace 是 append-only + 读时合并（`lib/trace/merge.py`），usage 换源要单独设计合并规则，留给下一轮。
- 本轮不删 tmux 注入：Mod 通道是首选，没有 Mod 监听的会话仍走 tmux，行为不变。
- 不改 27 个 settings 事件的 `hook_manager` 冷启动链路（映射表第 4 行）。

## Mod 能力（2.1.289，early access，接口可能随版本变化）
| 面 | 内容 |
|---|---|
| 事件 | tool.call/describe/check, prompt.submit/compose/attachment/context/section, session.start/end/append/receive/send/compact/measure, turn.start/step/complete, skill.prompt, agent.offer/spawn, command.run, ui.render/press/input, classic.<SettingsHook> |
| `$` | ui(status/toast/open pane/band/selection/copy), state(会话内)/store(跨会话), http.fetch(含 Unix socket), process.run/spawn(长期子进程流), model.complete/fork, tool.register, agent.register/spawn, command.register, prompt.submit/fill, session.append |
| 约束 | 运行环境里没有 Node/DOM，不能 import regin 的 Python，只能走 process.run 或 http.fetch；每个钩子预算 10s，只算自身执行时间，`await $` 期间不计；regin 的 web API 要 JWT，mod 得用 CLI 或 bridge token |

## 映射表（替代 / 新增 / 不适用）
| regin 现状 | 痛点 | Mod 方案 | 结论 | 优先级 | 已验证 |
|---|---|---|---|---|---|
| Agent Bridge：tmux send-keys，要过 pane 白名单校验；后台会话还得先 attach | 依赖终端字节和 pane 身份，脆弱 | session.start 起一个会话级 `$.process.spawn`（`regin bridge listen`），它用 bridge token 持有专用 NDJSON 流 `GET /api/bridge/listen`（不复用 `notifications.py` 的 SSE：那条要 JWT 票据和 ping 保活，违反 I2）→ 每行 `$.prompt.submit`（会话空闲时排队成新一轮）。断连靠 select 请求 socket 的 EOF 发现，不用心跳。斜杠命令（如优雅关闭的 `/exit`）仍走 tmux，直到确认 `$.prompt.submit` 会把它当命令执行；没有回执，流结束时队列里残留的帧审计为 `bridge_mod_delivery_lost` | 替代（消息） | P1 | regin 侧：是（真实 server + 被杀的 listen 子进程 e2e）；mod 侧：kit 内是；实时会话未验证 |
| turn_trace 从 transcript JSONL 回填 usage | 解析文件，滞后 | `turn.complete`（usage）、`turn.step`（每次请求）、`session.measure`（用量/限流主动推送） | 替代 | P1 | 否 |
| Agent SDK tier 只是为了用 can_use_tool 回答 AskUserQuestion，进程必须由 regin 启动 | 用户自己开的会话用不上 | `regin-ask`：`tool.call{tool:AskUserQuestion}` 钩子 `await $.http.fetch(answerUrl)` 取运营者回答，再 `next({...e, answers})` 改写（工具参数平铺在 `e` 上，没有 `e.input`）；`answers` 以完整问题文本为键，多选用 `", "` 连接 | 替代候选：任何会话都能用 | P2 | kit 内：是（`claude-code-tools` 的 `BuiltinToolInputs.AskUserQuestion` 有 `answers?: {}`；`claude plugin test regin-ask` 的 `ask rewrite reaches the tool with answers` 中引擎底层收到 `answers`）。**实时会话：否，已证伪**（F158 P0 Q2，CC 2.1.295，tmux 里的交互式 claude + `--plugin-dir` 探针 mod）：`tool.call` 里 `next({...e, answers})` 之后对话框照样弹出，人工所选覆盖注入值（注入 `Apple`，人工选 `Pear`，结果 `answers:{"Pick a fruit?":"Pear"}`）；只注入部分问题时，对话框里所有问题都没有预填（`☐ Metal  ☐ Gem`）；在 `tool.check` 再返回 allow 也没用，debug 日志写着 `the tool requires the person, so the plugins' allow is ignored`。所以 regin-ask 的改写在真实会话里不生效，F158 把 ask 留在 tmux 路径。旁证：SDK 的 `updated_input` 带 `answers` 已在 `lib/agent_sdk/answers.py` 端到端验证过。已知缺口：`$.http.fetch` 没有超时，答案源接了连接却不回时会一直等 |
| 27 个 settings 事件 × 每次都冷启动一个 `python -m hook_manager` | 每个事件都起进程 | 一个 mod 把事件转发给常驻 regin（http.fetch socketPath） | 替代候选，只对 Claude 生效 | P2 | 否 |
| statusline-command.sh / /live 网页卡片 | 终端里看不到 regin 状态 | AbovePrompt 横条 + `$.ui.status`；Pane 显示收件箱/当前 feature/task；`/regin-*` slash 命令 | 新增 | P2 | 横条：是（PoC） |
| rule_check_prompt 等到下一个 prompt 才重放违规 | 晚一轮 | `tool.call` 里 `await next(e)` 之后直接把违规拼进工具结果 | 改进 | P3 | 否 |
| send_to_user 是 stdio MCP，拿不到会话；由 PostToolUse 写库 | 绕两跳 | `$.tool.register`，在会话内拿到 session id 直接写 | 可替代，现有方案能用 | P3 | 否 |
| memory_recall / skill_experience 用 additionalContext 注入 | 无 | prompt.attachment / skill.prompt | 等价，不构成迁移动机 | — | — |
| block_mvn 等 gate | 无 | `tool.call` 返回 `{deny}` | 等价 | — | — |
| worktree_claude_sync、Codex/Kimi 链路 | 无 | — | 不适用 | — | — |


## 分发
Mod 就是插件，和 `regin-agents` 一样放进仓库里已有的本地 marketplace `regin-plugin/`（`regin-local`）：每个 mod 一个目录 `regin-plugin/plugins/<mod>/`，并在 `regin-plugin/.claude-plugin/marketplace.json` 里登记一条。用户 `/plugin marketplace add ./regin-plugin` 后按名安装。不另造部署命令。

Mod 源码只写在 `regin-plugin/plugins/<mod>/`，**不要**写进 `~/.claude/dev-mods/`：那个目录会在操作者的会话里弹出热加载确认框，无人值守的 run 会被它卡住。

## 不变式
- I1 每个 `regin-plugin/plugins/*` 目录在 `marketplace.json` 里恰有一条，反之亦然；每个都有 `.claude-plugin/plugin.json`。
- I2 Mod 通道是事件驱动的：监听者的在线状态 = 它那条流连接的生命周期，不用心跳、超时或轮询。
- I3 `deliver()` 对没有 Mod 监听者的会话，行为与本轮之前逐字节一致（tmux 路径、守卫、审计）。
- I4 监听者只收到发给自己 session id 的 prompt。
- I5 “立即发送”只走 Mod 通道：会话没有 Mod 监听者时，UI 不显示这个按钮，API 返回 409；**绝不**退回 tmux Escape（Escape 会把排队的 prompt 拉回编辑器，也就是现在“只能先 cancel 再重发”的根源）。
- I6 attribution 守卫只删 `Claude-Session:` 行和裸的 `https://claude.ai/code/session_…` 链接，其余文字逐字保留。

## Acceptance

```gherkin
Scenario: marketplace lists every plugin dir
  Given regin-plugin/plugins 下的每个目录
  Then marketplace.json 恰有一条同名记录，且目录里有 .claude-plugin/plugin.json

Scenario: unread count shows above the prompt
  Given `regin messages list --unread --json` 输出 5 条消息（未读、未 dismiss、非测试行——正是 `read --all` 清掉的范围；`stats` 的 unread 含测试行，按完按钮也到不了 0，故不用它计数）
  When 会话启动
  Then terminal 和 desktop 的 AbovePrompt 都显示 "regin inbox: 5 unread"

Scenario: band is empty when inbox is clear
  Given 输出 `[]`
  Then AbovePrompt 不显示 regin 横条

Scenario: listener streams only its own session's prompts
  Given session A 和 B 各有一个 `regin bridge listen --session <id>` 监听者
  When 向 A 投递 "hello"
  Then A 的监听者输出一行 JSON {"text":"hello",...}，B 什么也收不到

Scenario: deliver prefers a connected mod listener
  Given session A 有已连接的 Mod 监听者，也有可达的 tmux pane
  When deliver(A, "hi")
  Then prompt 经 Mod 通道送达，不发生 tmux send-keys，审计记录 channel=mod

Scenario: deliver falls back to tmux without a listener
  Given session A 没有 Mod 监听者
  When deliver(A, "hi")
  Then 走原有 tmux 路径，结果与本轮之前一致

Scenario: listener disconnect unregisters the channel
  Given A 的监听者断开
  When deliver(A, "hi")
  Then 走 tmux 路径（或在无 pane 时返回 "no reachable session"）

Scenario: listener line becomes a submitted prompt
  Given regin-bridge mod 已在 session.start 起了监听进程
  When 监听进程输出一行 {"text":"hello"}
  Then mod 调用 $.prompt.submit，文本为 "hello"

Scenario: ask rewrite reaches the tool with answers
  Given regin-ask mod 的 tool.call 钩子挂在 AskUserQuestion 上，答案源返回一个答案
  When 模型调用 AskUserQuestion
  Then 下层收到的 tool.call 带着该答案（next({...e, answers}) 被链路接受）

Scenario: other tools pass untouched
  When 模型调用 Bash
  Then regin-ask 不改写 input，直接 next(e)

Scenario: send now aborts the running turn
  Given regin-bridge mod 收到过 turn.start，turnId 为 T，且还没有 turn.complete
  When 监听进程输出一行 {"op":"abort"}
  Then mod 调用 $.turn.abort({ turnId: T })，并且不提交任何 prompt

Scenario: abort frame without a running turn is ignored
  Given 最近一次 turn 已经 turn.complete
  When 监听进程输出一行 {"op":"abort"}
  Then 不调用 $.turn.abort

Scenario: send now goes over the mod channel
  Given session A 有已连接的 Mod 监听者
  When POST /api/sessions/A/bridge-send-now
  Then A 的监听者收到 {"op":"abort"}，响应 delivered=true、channel=mod，不发生 tmux send-keys

Scenario: send now refuses without a mod listener
  Given session A 没有 Mod 监听者
  When POST /api/sessions/A/bridge-send-now
  Then 返回 409，不发生 tmux send-keys（I5）

Scenario: live state reports the mod listener
  When GET /api/sessions/A/live-state
  Then 响应里的 mod_listener 与 A 当前是否有已连接的监听者一致

Scenario: queued prompts offer send now only with a mod listener
  Given 会话有排队的 prompt
  When live-state 的 mod_listener 为 true
  Then 排队列表显示“立即发送”，点击后 POST bridge-send-now
  When mod_listener 为 false
  Then 不显示“立即发送”

Scenario: commit attribution drops the session trailer
  Given attribution.text kind=commit 的文本里有 "Claude-Session: https://claude.ai/code/session_x"，另有 Co-Authored-By 行
  Then 返回的文本没有 Claude-Session 行，Co-Authored-By 行原样保留

Scenario: pr attribution drops bare session links
  Given kind=pr 的文本里有一行裸的 https://claude.ai/code/session_x 链接
  Then 这一行被删掉，其余行逐字保留

Scenario: other attribution text passes untouched
  Given 文本里没有会话 trailer 或链接
  Then 返回的文本与 next(e) 的结果逐字相同

Scenario: messages list prints unread as json
  When 运行 `regin messages list --unread --json`
  Then 输出未读消息的 JSON 数组，每项是 `store` 序列化的整行，含 id、msg_type、title、created_at

Scenario: messages read marks the given ids read
  When 运行 `regin messages read <id>`，或 `regin messages read --all`
  Then 对应消息被标为已读，`regin messages list --unread` 不再列出它们；既不给 id 也不给 `--all`（或两者都给）以退出码 2 拒绝

Scenario: regin inbox command opens the inbox pane
  Given 有 2 条未读消息
  When 运行 /regin-inbox
  Then 打开一个 Pane，列出这 2 条消息的标题

Scenario: mark all read clears the pane
  Given 收件箱 Pane 已打开
  When 按下 "Mark all read"
  Then 运行了 `regin messages read --all`，Pane 显示收件箱已清空，横条不再显示
```

**Base: `f125/integration`**（不单独切 `f134/integration`，每个切片都合回 `f125/integration`）。

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Mods ship in regin-local | PoC `regin-band` 从 `~/.claude/dev-mods/ae818f96-650d-4339-b2b0-05c51597ebe0/regin-band/` 搬进 `regin-plugin/plugins/regin-band/`（含 `.claude-plugin/plugin.json`、`hooks/`、`types/`、`register.test.tsx`），场景标题改成 Acceptance 里的英文名；`marketplace.json` 登记 regin-band；`tests/mods/test_marketplace.py` 断言 I1；`regin-plugin/README.md` 写明 mod 的安装方式 | 分发, PoC, I1 | test_marketplace_lists_every_plugin_dir, test_unread_count_shows_above_the_prompt, test_band_is_empty_when_inbox_is_clear | — | — | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/mods && claude plugin validate regin-plugin/plugins/regin-band && claude plugin test regin-plugin/plugins/regin-band |
| S2 | Bridge over Mod | regin 侧：`regin bridge listen --session <id>`（bridge token 认证，订阅服务端事件，每条发给该 session 的 prompt 输出一行 JSON；连接存续即注册、断开即注销，I2）；`lib/agent_bridge/delivery.deliver()` 在该 trace 有已连接监听者时走 Mod 通道并审计 `channel=mod`，否则 tmux 路径不变（I3）；监听者路由按 session id（I4）。Mod 侧：`regin-plugin/plugins/regin-bridge/`，session.start 用 `$.process.spawn` 起监听进程，每行 → `$.prompt.submit`，进程只起一次；登记进 marketplace.json | 映射表第 1 行, I2, I3, I4 | test_listener_streams_only_its_own_sessions_prompts, test_deliver_prefers_a_connected_mod_listener, test_deliver_falls_back_to_tmux_without_a_listener, test_listener_disconnect_unregisters_the_channel, test_listener_line_becomes_a_submitted_prompt | — | S1 | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/mods tests/agent_bridge tests/web && claude plugin validate regin-plugin/plugins/regin-bridge && claude plugin test regin-plugin/plugins/regin-bridge |
| S3 | AskUserQuestion rewrite probe | `regin-plugin/plugins/regin-ask/`：`tool.call{tool:AskUserQuestion}` 钩子从可注入的答案源（userConfig `answerUrl`，经 `$.http.fetch`）取答案，`next({...e, answers})` 改写；其它工具直接 `next(e)`；`*.test.ts` 覆盖两个场景；登记进 marketplace.json。查 `claude-code-tools` 类型里 AskUserQuestion 的 input 是否有答案字段，把结论（kit 内已验证 / 实时会话未验证，各附证据）写回本 spec 映射表第 3 行的「已验证」列 | 映射表第 3 行 | test_ask_rewrite_reaches_the_tool_with_answers, test_other_tools_pass_untouched | — | S1 | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/mods && claude plugin validate regin-plugin/plugins/regin-ask && claude plugin test regin-plugin/plugins/regin-ask |
| S4 | integrate | 检查每一片都已合回 `f125/integration`，无人落零 commit | Invariant 1 | test_every_slice_merged_back | — | S2, S3 | f125/integration | goal-verified-treenav | — |
| S5 | migrate + doctor | 跑 `.venv/bin/python cli/regin.py migrate`，确认这个安装没落后 | R1 | test_migrate_leaves_no_schema_finding | — | S4 | f125/integration | goal-verified-treenav | .venv/bin/python cli/regin.py migrate |
| S6 | verify | 在 `f125/integration` 上跑全量 pytest（排除基线红：`trace_payload` 6 条、并行下偶发的 `test_a_reserved_run_is_not_reachable_yet`），外加三个 mod 的 `claude plugin test`。本 feature 不改前端，故不跑 Playwright。复现于基线的失败只报告不修 | Acceptance, I1–I4 | test_full_suite_passes | — | S5 | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile -k 'not trace_payload and not a_reserved_run_is_not_reachable_yet' && claude plugin test regin-plugin/plugins/regin-band && claude plugin test regin-plugin/plugins/regin-bridge && claude plugin test regin-plugin/plugins/regin-ask |
| S8 | Send now over Mod | regin 侧：`POST /api/sessions/<id>/bridge-send-now`：有 Mod 监听者时向它投递控制帧 `{"op":"abort"}`，审计 `channel=mod`；没有监听者时返回 409，不碰 tmux（I5）；`live-state` 增加 `mod_listener` 字段。Mod 侧：`regin-bridge` 用 `$.state` 记录运行中的 turnId（turn.start 写入，turn.complete 清空），收到 abort 帧调 `$.turn.abort({turnId})`，不提交 prompt，CC 自己会接着执行队列里的 prompt。前端：`TraceQueuedPrompts.vue` 在 `mod_listener` 为 true 时显示“立即发送”（打断当前 turn，让排队的 prompt 马上执行），新增 `frontend/tests/trace-queued-send-now.spec.js` | 第二轮 #1, I2, I4, I5 | test_send_now_aborts_the_running_turn, test_abort_frame_without_a_running_turn_is_ignored, test_send_now_goes_over_the_mod_channel, test_send_now_refuses_without_a_mod_listener, test_live_state_reports_the_mod_listener, queued prompts offer send now only with a mod listener | — | — | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/mods tests/agent_bridge tests/web && claude plugin test regin-plugin/plugins/regin-bridge && cd frontend && ./node_modules/.bin/playwright test tests/trace-queued-send-now.spec.js tests/trace-queued-edit.spec.js |
| S9 | Attribution guard | `regin-plugin/plugins/regin-attribution/`：`attribution.text` 钩子，kind 为 commit 或 pr 时先 `await next(e)`，再删掉 `Claude-Session:` 行和裸的 `https://claude.ai/code/session_…` 链接，其余文字逐字保留（I6）；其它 kind 直接 `next(e)`；`*.test.ts` 覆盖三个场景；登记进 marketplace.json（I1） | 第二轮 #2, I1, I6 | test_commit_attribution_drops_the_session_trailer, test_pr_attribution_drops_bare_session_links, test_other_attribution_text_passes_untouched | — | — | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/mods && claude plugin validate regin-plugin/plugins/regin-attribution && claude plugin test regin-plugin/plugins/regin-attribution |
| S10 | Inbox pane | CLI：`regin messages list [--unread] [--json]` 和 `regin messages read <id>... / --all`，都调 `lib/agent_messages/store` 已有的 `list_inbox`、`mark_read`、`mark_all_read`。Mod：`regin-band` 注册 `/regin-inbox` 命令，打开一个 Pane 列出未读消息（标题、类型、时间），带 “Mark all read” 按钮；横条加一个打开 Pane 的按钮；按下按钮后刷新 `$.state` 里的计数（计数与 Pane 列表同取自 `messages list --unread --json --limit 201`，超过 200 显示 "200+"） | 第二轮 #3, I1 | test_messages_list_prints_unread_as_json, test_messages_read_marks_the_given_ids_read, test_regin_inbox_command_opens_the_inbox_pane, test_mark_all_read_clears_the_pane | — | — | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/mods tests/cli tests/trace && claude plugin validate regin-plugin/plugins/regin-band && claude plugin test regin-plugin/plugins/regin-band |
| S11 | integrate (round 2) | 检查 S8–S10 都已合回 `f125/integration`，没有哪一片零 commit | Invariant 1 | test_every_slice_merged_back | — | S8, S9, S10 | f125/integration | goal-verified-treenav | — |
| S12 | verify (round 2) | 在 `f125/integration` 上跑全量 pytest（排除基线红：`trace_payload` 6 条、并行下偶发的 `test_a_reserved_run_is_not_reachable_yet`）、四个 mod 的 `claude plugin test`、两个 queued 相关的 Playwright spec。复现于基线的失败只报告不修 | Acceptance, I1–I6 | test_full_suite_passes | — | S11 | f125/integration | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && .venv/bin/python -m pytest -q -n auto --dist=loadfile -k 'not trace_payload and not a_reserved_run_is_not_reachable_yet' && claude plugin test regin-plugin/plugins/regin-band && claude plugin test regin-plugin/plugins/regin-bridge && claude plugin test regin-plugin/plugins/regin-ask && claude plugin test regin-plugin/plugins/regin-attribution && cd frontend && ./node_modules/.bin/playwright test tests/trace-queued-send-now.spec.js tests/trace-queued-edit.spec.js |




## Prompt 队列：感知与“立即发送”
| 需求 | regin 现状 | Mod 方案 | 已验证 |
|---|---|---|---|
| 感知排队的 prompt | `lib/trace/queued_prompts.py` 回放 transcript 里的 `queue-operation`，靠两条补丁规则纠偏（会话忙时输入的 prompt 不触发 UserPromptSubmit） | `prompt.submit` 对每次提交都触发，turn 进行中输入的也算（`turnId` 有值即是；`wait` 表示用户按了 ctrl+x enter；`origin` 是来源）；`session.append` 是入对话（出队）信号 | 否 |
| 读或改 CC 内部队列 | — | **没有对应的 op**；`popAll`（把排队 prompt 拉回编辑器）也没找到对应事件 | — |
| 立即发送 | 只能先 cancel 再重发 | `$.prompt.submit` 不行（插件提交的一律等空闲）；`$.session.append` 写 user 行可以注入运行中的 turn，从下一次模型请求起生效 | 否 |

方案：**由 mod 接管队列。** `prompt.submit` 里 `turnId` 有值且 `wait=false` 的 prompt，存进 mod 自己的 `$.state` 队列，对 CC 返回 `{drop}`；regin UI 点“立即发送”时调 `$.session.append`；什么都不点的话，在 `turn.complete` 后由 mod 调 `$.prompt.submit` 依次发出。
代价：注入的行带 isMeta，transcript 里不会显示成用户输入；只支持纯文本；排队显示要由 mod 自己画。
第一步：先写一个只观察的 PoC，拿它记到的事件和 `queued_prompts.py` 的回放结果对照。

## 后台 shell / 后台任务
| 启发式（现状） | 位置 | 可靠的替代 | 需要 mod 吗 | 本轮 |
|---|---|---|---|---|
| 输出文件路径只出现在 tool_result 的文本里，钩子看不到，只能从某条完成通知推断目录 | `background_shells.tasks_dir/output_path` | mod 在 `tool.call{Bash}` 里 `await next(e)`，直接拿到结构化结果（`backgroundTaskId`、`backgroundedByUser`、`timedOutAfterMs`、`backgroundedToDeliverMessage`）和结果文本（含路径） | 是 | 后续 |
| 判断结束：读输出文件尾部的 `[killed]`/`[exited with code N]`；约 2/3 的 shell 收不到 task.notification，被 kill 的不通知任何人 | `background_shells.terminal_outcome` | **不需要 mod**：classic `Stop` 的 `background_tasks` 是 CC 任务注册表里 running/pending 且已转后台的任务快照（`{id,type,status,description,command?}`；子 agent 与主会话共用一个注册表）。roster 仍记为 running、但快照里没有的 shell → 已结束：输出文件有结束标记就用它给的原因，没有就记 `stopped`（CC 自己对“结束但没说怎么结束”的用词）。不存上一次快照：已持久化的启动记录就是比较基准。字段缺失（Kimi/Codex/旧版 CC）什么都不做 | 否 | 已做：`hook_manager/handlers/shell_lifecycle.handle_stop` → `repair.settle_shells_out_of_flight`。只挂 `Stop`，不挂 `SubagentStop`（主 turn 结束就够，少一个触发点）。实时会话：hook_manager + 真实 server 合成负载已验证；真实 CC 的 Stop 负载未在 hook 日志里抓到过（`trace_payload` 日志只记 Pre/PostToolUse） |
| 结束原因 | 同上 | 通知行的 `UserMessage.e.props.task.status`（completed/failed/killed）+ `toolUseId` 精确关联发起它的调用；`tool.call{TaskStop}` 直接记录 kill | 是（UserMessage）/ 否（TaskStop 用 PostToolUse 记） | TaskStop 已做：`task_type == 'local_bash'` 时把仍在 running 的 shell 记为 `killed`（`repair.record_shell_kill`），`tool.TaskStop` span 现在带 `task_id`/`task_type`。UserMessage 后续 |
| 实时进行中，不只在 Stop 时 | 读文件尾 | mod 在 `turn.complete` 和 `tool.call` 后都没有任务列表可查；`$.agent.list()` 只列子 agent，不列 shell | **缺口**：mod 也没有 shell 列表接口 | — |
| 子 agent 的 SubagentStop 丢失 → sweep | `subagent_sweep` | `$.agent.list()` 给出状态（pending/running/waiting/idle/completed/failed/killed） | 是 | 后续 |
| ScheduleWakeup 恢复关联靠时间顺序 | `wakeup_links.py` | `prompt.submit` 的 `origin`（`loop_wakeup`/`scheduled-trigger`）直接标出唤醒来源 | 是 | 后续 |

三个写入方共用 `notification_span_id(task_id)`，store 按 `(trace_id, span_id)` upsert：只给 roster 仍记为 running 的 shell 写，所以不会覆盖真实结果；之后到达的真实通知会替换这一行。Stop 钩子里逐个 post，ingest 拒收一次就停，剩下的留给下一次 Stop（server 挂掉时不必为每个 shell 都等完整的重试预算）。已知缺口：同一 session id 有两个存活进程（原进程还在跑时又 resume）时，后一个进程的 Stop 会把前一个进程仍在跑的 shell 记成 `stopped`。

## 第二轮：用 Mod 把现有功能做得更好
| # | 现有功能 | 现在的问题 | Mod 方案 | 本轮 |
|---|---|---|---|---|
| 1 | /live 排队列表 | 想让排队的 prompt 立即执行，只能先 cancel 再重发：tmux Escape 会把排队的 prompt 拉回编辑器 | 控制帧 → `$.turn.abort({turnId})`：打断当前 turn，不留中断标记，CC 自己接着跑队列（`$.prompt.submit` 不能插队，所以不走它） | S8。实时会话已验证（CC 2.1.289，探针 mod，tmux 里的交互式 claude）：turn 进行中提交的 prompt（`turnId` 有值）→ `$.turn.abort` → 该 turn 以 `reason: aborted` 结束 → CC 立即用排队的 prompt 开始下一轮并作答，没有把它拉回编辑器 |
| 2 | CLAUDE.md “commit 里不要有 Claude-Session” | 只靠 agent 自觉 | `attribution.text` 在 CC 生成署名文本那一步就删掉 | S9 |
| 3 | 收件箱只能在网页上看 | 终端里只看得到未读数 | `/regin-inbox` Pane + 标记已读 | S10 |
| 4 | skill 调用追踪（skill_read / skill_invoke / skill_launch 三个启发式） | 读 content.md、展开 slash 命令、Skill 工具，三条路各猜一次 | `skill.prompt` 在 skill 展开时触发一次，带 skill 名 | 后续 |
| 5 | 权限决策卡片 | 网页上回答权限，靠 tmux 敲键 | `tool.check` 钩子等 regin 的裁决，返回 `{decision}` | 后续（同 regin-ask 模式） |
| 6 | 压缩后丢失 feature/task 上下文 | 压缩后只能靠下一个 prompt 补 | `session.compact` 改写 `instructions`，把当前 feature/task 带进摘要 | 后续 |
| 7 | 用量 / 限流告警 | 回填 transcript，事后才知道 | `session.measure` 主动推送，越过阈值就走 push 通道 | 后续 |
| 8 | 排队 prompt 的感知（`queued_prompts.py` 回放） | 靠补丁规则纠偏 | 用 `prompt.submit` 和 `session.append` 观察，再和回放结果对照 | 后续（先做只观察的 PoC） |




