# F145 kimi-acp会话修复

## 意图

Kimi 会话（尤其是 regin 通过 ACP 启动的 task run）在 Session Trace 详情页上必须和 Claude 会话一样可读：能看到 prompt、按 turn 分组、有标题，状态也要如实（失败就显示失败，结束了就不再是 Working）。本 spec 写下 2026-10-08 实时排查得出的缺陷，每条都有证据会话。早先已修的问题（defect #271 的 live status 与 pending 降级，#274 的 `/skill:` 命令名）不在本 feature 范围内。

**非目标**：不处理 subagent 工具调用的归属。90b67612 上的 75 个 hook tool id 在 DB 里 `tool_use_id IS NULL`，原因是 `kimi_subagents` 把它们重新归到了 `:agent-0` 下，属于设计行为，并没有丢 span。也不引入任何基于时间的结束判定（CLAUDE.md 的 root-cause discipline）。

## 证据

| 会话 | 来源 | 现象 |
|---|---|---|
| `session_a4171fa6…`、`session_b92d9f36…` | agent_runs 390/389，ACP，F144 worktree，launch prompt = `/skill:goal-verified …` | `prompts=0`，`title=NULL`；map 中 54 个 `assistant.thinking` 是根节点，61 个 tool 挂在 thinking 下；kimi `state.json.lastPrompt` 有完整 prompt；hook-payloads 只有 Pre/PostToolUse，**没有 UserPromptSubmit** |
| `session_fb5709ea…`、`124623bc…`、`7ab7be50…` | hello-trace 探针，`/skill:hello-trace` | 有 `turn` 但没有 `prompt`，标题为空 |
| `session_06409f92…`、`f998d62c…`、`5fa30691…`、`582ca87f…` | agent_runs 388/387/385/384，ACP | kimi 回复 "Unknown ACP command"（e36fa9dd 之前），wire 只有 metadata + set_binding；run 却记为 `exited / end_turn`，session 记为 `ended / one-shot turn ended`，trace 只有 `session.start` + `git_status` |
| 12 个 kimi 会话 | 无 agent_runs 行（探针或外部启动） | `status='active'` 永不收敛，最早的从 2026-10-07T03:06 至今 |
| 对照 `session_925426a5…` | ACP，普通文本 prompt | 有 UserPromptSubmit，结构正常：所有 span 都在 `prompt` 下 |

wire 中 `turn.prompt.origin` 的分布（2026-09 以后的全部会话）：`user` 83，`skill_activation/user-slash` 10，`system_trigger` 4。

## 根因

```
kimi /skill:<name> <args> (ACP 或 TUI)
  ├─ hook: UserPromptSubmit 不触发                        → 没有 hook prompt span、session_title
  └─ wire: turn.prompt{origin:{kind:skill_activation, trigger:user-slash, skillName, skillArgs}}
        └─ kimi_transcript._Scan._on_prompt: kind ∉ {None,'user'} → return   → 也没有 transcript prompt anchor
```

两条 prompt 来源都落空，于是 merge 找不到 prompt 祖先，整棵树平铺。

## 形状

```python
# lib/trace/kimi_transcript.py — 分类 turn.prompt，替代现在的 "kind ∉ {None,'user'} → drop"
PromptOrigin = Literal['user', 'user_slash_skill', 'harness']

def classify(origin: dict | None) -> PromptOrigin: ...
# user_slash_skill 的 prompt 文本 = f"/{skillName} {skillArgs}".rstrip()
#   （与 Claude 的 "/<skill> <args>" prompt 形状一致；trace UI 不需要新分支）
```

### 决策表：turn.prompt → regin

| origin.kind | origin.trigger | 记为 prompt anchor | 文本 | 作为会话标题候选 |
|---|---|---|---|---|
| 缺省 / `user` | — | 是（现状） | typed text | 是 |
| `skill_activation` | `user-slash` | **是（新）** | `/<skillName> <skillArgs>` | **是** |
| `skill_activation` | 其他或缺省 | 否 | — | 否 |
| `system_trigger` / 其他 | — | 否（现状） | — | 否 |

### 决策表：ACP launch turn 的结算

| stopReason | 本 turn 在 wire 上是否开始（`agent.turn.started`，或 ACP 收到任何 `tool_call`/`agent_thought_chunk`） | run 状态 | detail |
|---|---|---|---|
| `end_turn` | 是 | 现状（exited，等 task finish） | `end_turn` |
| `end_turn` | **否** | **failed** | agent 回的 `agent_message_chunk` 文本（如 `Unknown ACP command: …`），没有时为 `prompt rejected` |
| 其他 | — | failed（现状） | stopReason |

"turn 是否开始"由 ACP 线上事件判定（`AcpActivity` 已经在 fold 这些事件），不看时钟。

## 规则 / 不变量

- I1：wire 里有至少一条可记录的 turn.prompt（按上面的决策表）的 kimi 会话，结构化 map 中至少有一个 `prompt` span；它的 turn / thinking / tool 都挂在这个 prompt 下，不在根节点。
- I2：同一个 prompt 只出现一次。hook prompt 与 transcript anchor 已有的去重机制不变；本 feature 只会给原本**没有** hook prompt 的那类输入（user-slash skill）新增 transcript anchor，不引入新的去重键。
- I3：`sessions.prompts` 与 `sessions.title` 对 slash-skill 会话的处理，和普通 prompt 会话一致（title 取首个 prompt）。
- I4：一个 ACP launch prompt 如果 agent 从未开始执行，run 结算为 `failed`，并带有 agent 的原文回复；task 不会在任何情况下被当作"正常结束、等报告"挂起。
- I5：被拒的 prompt 在 trace 上留下一个可见的 ERROR span（带拒绝原文），这样详情页不再只剩 `session.start`。
- I6：kimi 的 `Stop` 只有在主 wire 显示当前 turn 已结束（`agent.turn.ended` / `turn.ended`，或被 `turn.cancel` 中断）时，才把 `turn` span 标为边界（`hook_event='Stop'`），让 `turn_state` 判定 idle。子 agent 之前触发的 parent Stop（turn 仍开着），以及没有 turn 生命周期记录的 1.4 wire，都不算边界。判定来源是 `TranscriptUsage.main_turn_open`，不看时钟。

## 验收（Gherkin）

```gherkin
Feature: kimi slash-skill prompt 进入 trace

  Scenario: ACP 启动的 /skill: run 有 prompt 和标题
    Given 一个 kimi wire.jsonl，其 turn.prompt 的 origin = {kind: skill_activation, trigger: user-slash, skillName: "hello-trace", skillArgs: "reply OK"}
    And 该会话没有 UserPromptSubmit hook
    When 会话被摄取并请求 /api/sessions/<sid>/map
    Then 恰好有 1 个 prompt span，文本为 "/hello-trace reply OK"
    And 所有 assistant.thinking / tool.* span 的祖先链里都有这个 prompt
    And sessions.prompts = 1，sessions.title 以 "/hello-trace" 开头

  Scenario: 非用户触发的 skill_activation 仍然不是 prompt
    Given turn.prompt 的 origin = {kind: skill_activation, trigger: "model"}（或 kind = system_trigger）
    Then read_usage_kimi(...).prompt_texts 为空

  Scenario: 普通 prompt 不重复
    Given 一个同时有 UserPromptSubmit hook 和 origin.kind=user turn.prompt 的会话（如 925426a5 的形状）
    Then map 中 prompt span 的数量与修复前相同

  Scenario: 存量会话可以修复
    Given 已摄取的 session_a4171fa6…（prompts=0）
    When 跑现有的 kimi transcript 回填 / repair 路径
    Then 它得到 1 个 prompt span，原本挂在根节点的 thinking span 改挂到 prompt 下

Feature: ACP 拒绝的 launch prompt 结算为失败

  Scenario: agent 在 0 ms 内以 end_turn 拒绝
    Given fake ACP server 对 session/prompt 只回一个 agent_message_chunk "Unknown ACP command: /x"，然后 stopReason=end_turn，期间不发 turn 开始事件
    When launch_run(..., one_shot=True)
    Then handle 结算 RunOutcome(status="failed", detail 含 "Unknown ACP command")
    And agent_runs.status = "failed"
    And trace 中有一个 status_code=ERROR 的 span 带上述原文

  Scenario: 正常 turn 不受影响
    Given fake server 先发 tool_call 再 end_turn
    Then run 不会因本规则结算为 failed（现有 launcher 测试全绿）
```

## 未决 / 后续（本 feature 不实现）

- 没有 agent_runs 行的 kimi 会话（12 个）永远停在 `active`：kimi 在 ACP 和 `-p` 下都不发 SessionEnd，而 regin 不持有这些进程。要收敛只能靠一个事件或标识信号（例如 kimi 的会话锁或进程退出），时钟窗口被禁止。这件事需要单独立 defect 来决定信号源，本 spec 只记录现象。

