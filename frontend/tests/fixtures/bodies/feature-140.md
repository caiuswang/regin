# Task 等操作支持 kimi（启动侧 provider 解耦）

> 原始需求：目前 Task 等操作只支持 claude code，支持一下 kimi。如果原来的操作深度绑定 claude code，先解耦，这样后续支持其他家的产品也方便使用。

## Intent

regin 和 AI CLI 打交道分两侧。

- **采集侧**：hooks、transcript 解析、skills、subagent 归一化。这一侧早就走 provider 抽象，kimi 是注册表里的正式成员。
- **启动侧**：task run 启动、arm 参数、/live 新会话与 Resume、bridge 拉起与 pane 守卫、skill 调用、worktree 准备。这一侧原本只认 claude。

本 feature 让启动侧也走 provider 抽象，交付标准是 kimi 用户能在 UI 里选 kimi arm 一个 task，跑起来，正常 settle。可以走 ACP 通道，也可以走 tmux，后者重启 serve 不中断。此后接入第三家只需注册 adapter。

## 现状（2026-10-07，本期交付后）

| 层 | 状态 |
|---|---|
| 采集侧 | 无改动，kimi 完整 |
| 启动能力 | 每个 provider 声明 `launch_capabilities()`：claude 支持 sdk/tmux 与 model/permission_mode/effort；kimi 支持 sdk(ACP)/tmux 与 model；codex/generic 为空，arm 时 400 |
| ACP 状态 | 按 D9 从 `session/update` 流读出 live status：当前 tool 标题、Thinking / Responding、turn 用时、`usage_update` 的 context 占比，经 `run_live_state` 进入 `/map.live_screen`；run 报告仍在进行的 tool call 不会被降级为 interrupted |
| ACP run | 按 D6 settle；one-shot 会关闭通道；model 走 `session/set_model`；images 作为 content block 发送；cwd 用绝对路径 |
| tmux run | argv、pane 输入、session 锚定都由 adapter 提供；kimi 按 D5 处理；pane 守卫按 `pane_commands` 放行；重启 serve 后 run 仍为 LIVE（真机已验证） |
| Skill / workspace | skill 调用由 provider 生成（D7）；skill 目录与 worktree 同步按 provider 处理；kimi 的 workspace-trust 预写按 D8 |
| UI / API | TaskArmSheet、SliceArmSheet、LiveLaunchSheet 都能选 provider，只显示该 provider 声明的字段；`launch-options.providers` 带 `available` |
| /live 与 Resume | 按会话自己的 provider 解析；kimi 会话通过 ACP `session/load` 恢复 |
| 真机验收 | 在 master 上通过 HTTP API arm `provider=kimi`，ACP 与 tmux 各跑一个 task，都由 agent 自己 `task finish` settle 为 done，trace 正常落库 |

## 决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | arm 不写 provider 时默认 `claude`，不跟随 `active_provider` | 存量 arm 与自动 fire 行为不变；provider 的选择显式写在 arm 上 |
| D2 | `launch` 取值保持 `sdk` / `tmux`，不迁移数据。`sdk` 的含义改为“provider 的结构化通道”：claude 是 claude_agent_sdk，kimi 是 ACP。每个 provider 用 `launch_capabilities()` 声明自己支持哪些取值和哪些参数字段 | 词表由 adapter 声明，共享代码不出现 provider 分支 |
| D3 | 没声明任何启动通道的 provider（codex / generic），arm 时就 400 拒绝 | 不允许静默退化成 claude |
| D4 | **tmux arm 纳入本期**，推翻原“第二期可选”。kimi 的 tmux run 活在 regin 的 tmux server 上，重启 serve 不受影响 | ACP run 是 serve 的子进程，重启即死；交付物要求可正常运行 |
| D5 | kimi tmux 的 trace 锚定：先用一次短 ACP `session/new`（cwd 取 realpath）拿到真实 id 再关闭，然后在 pane 里 `kimi --session=<id> --yolo` 恢复。首轮 prompt 等 composer 出现后以 bracketed paste 贴入，看到回显或新的 `[paste #N …]` 占位后**用 C-m 提交**，不截断（finish 指令在末尾）；看不到就不提交、杀掉 session、attempt 记 failed | 真机实测（kimi 2.1.1，2026-10-07）：ACP 新建的空会话能被 `--session=<id>` 在 pane 里恢复，id 不变，trace 落在同一 id；composer 约 1.7s 出现；长 paste 折叠为 `[paste #N +M lines]` / `[paste #N C chars]`，9 KB 完整送达；多行 paste 以 `/skill:<name>` 开头照样展开 skill；`/tmp/x` 下建的会话在 cwd 解析为 `/private/tmp/x` 的 pane 里报 "created under a different directory" 并退出，所以锚定用 realpath。备选的 `kimi -p` 首跑路径不需要 |
| D6 | ACP run 的 settle 与 SDK arm 对齐：launch turn 结束本身不 settle，等 `task finish` 上报或通道关闭；stopReason 不是 `end_turn` 且没有 finish 上报时记为 failed | 避免 Stop 被记成 done、在 agent 说完之前就把下游 fire 出去 |
| D7 | Skill 调用归 provider：`AgentProvider.skill_invocation(name, args, also)`。claude 维持 `/name args` + “用 Skill tool 加载” 的 also 行；kimi 用 `/skill:<name> args`，also 行让 agent 去读 `.kimi-code/` 或 `.agents/` 下的 `skills/<name>/SKILL.md`。不需要 SKILL.md 回退 | 真机实测（kimi 2.1.1 ACP，2026-10-07）：`/skill:probe-skill hello-args` 回出只写在 SKILL.md 正文里的口令且零 tool call；`/probe-skill` 也展开；不带斜杠的对照问法答 UNKNOWN，说明正文只经展开进入上下文。`available_commands_update` 把它列为 `skill:probe-skill`。选 `/skill:` 形式是为了避开与内建命令重名 |
| D8 | channel 与 tmux 两种 launch 之前都调用 `provider.prepare_workspace(cwd)`；kimi 的实现预写 `~/.kimi-code/workspace-trust/wd_<basename>_<sha256(realpath)[:12]>`，已存在就不动 | 哈希公式已用现有 `wd_regin_5bbe3b09626a` 核对；不预写的话新 worktree 首启会被 Trust 弹窗阻塞 |
| D9 | ACP run 的 live status 来自 ACP 线上的 `session/update`（`tool_call` / `tool_call_update` / `agent_thought_chunk` / `agent_message_chunk` / `usage_update`），由 `AcpActivity` 折叠成与 tmux pane、SDK runner 相同的 `ScreenState`；run 仍报告进行中的 call（ACP `toolCallId` 去掉数字前缀 `0:` 即 hook 的 tool_use_id）不按静默时长降级 | hooks 与 transcript 在长 tool call、长思考、或卡在无人能完成的 MCP OAuth 时都静默，只看 span 时间，会话要么只显示 Working、要么被判 inactive，pending 的 call 被画成 interrupted。真机实测（kimi 2.1.1 ACP，2026-10-08）：tool call 先以裸工具名宣告，再改标题为 `Running: <cmd>` / `Reading <file>` 等，状态 pending→in_progress→completed/failed；hook 的 `tool_call_id` 等于 ACP id 去掉 `0:` 前缀 |

## 非目标

- 不重写采集 / trace 侧；不动 TaskOutput / TaskCreate / TodoList 的归一化；不动 hook 安装与 trace 重扫的 provider 门控。
- 不交付 codex / generic 的启动支持（D3 拒绝它们即可）。
- ACP 线上不做真 steer：协议不暴露。turn 内注入沿用 cancel → re-prompt。
- 不为 kimi 造 Claude 式的 permission_mode / effort 词表。
- 不做 topic 提案 / grader 的 kimi 启动；它们已有 subprocess 兜底。

## 形状

```python
class LaunchCapabilities(TypedDict):
    launch_modes: list[Literal["sdk", "tmux"]]   # 空列表 = 不能启动（D3）
    fields: list[Literal["model", "permission_mode", "effort"]]
    binary: str                                   # "claude" | "kimi"
    pane_commands: list[str]                      # pane 守卫放行的进程名；kimi 是 node 脚本，pane 报 `node`
    labels: dict[str, str]                        # {"sdk": "ACP"} 等，供 UI 显示
    models: list[str]                             # model 菜单里的别名；kimi 为空，只能填自定义 id
    default_model: str                            # 服务端配置的默认 model（claude 为 agent_sdk.model）

class AgentProvider:
    def launch_capabilities(self) -> LaunchCapabilities: ...
    def skill_invocation(self, name: str, args: str, also: list[str]) -> str: ...
    def prepare_workspace(self, cwd: Path) -> None: ...      # 默认 no-op
    def skill_catalog_layout(self) -> SkillCatalogLayout: ...  # skill_dirs / command_dirs / plugins
    def project_config_dir(self) -> str: ...                 # worktree 要同步的目录：.claude / .kimi-code
    # tmux arm
    def pane_session_id(self, cwd: str, env: dict[str, str]) -> str: ...  # 默认 uuid4；kimi 走 ACP session/new（D5）
    def pane_argv(self, session_id: str, *, model="", effort="", permission_mode="",
                  prompt="", resume=False) -> list[str]: ...  # claude 的 argv 原样搬入；kimi 不收 prompt
    def pane_input(self) -> PaneInput | None: ...  # None = prompt 在 argv 里；kimi: ready / paste_chip / submit_key=C-m

class PaneInput:  # frozen dataclass
    ready: str        # 原始 capture 上匹配：composer 可输入
    paste_chip: str   # 长 paste 的占位，group 1 是进程内单调递增的计数
    submit_key: str
```

`GET /api/agent-runs/launch-options` 增加 `providers: [{id, display_name, binary, available, launch_modes, fields, labels, models, default_model}]`，其中 `available = which(binary) is not None`。`GET /api/agent-runs/skill-options?provider=<id>` 按 provider 列 skill。

## 不变量

1. 共享代码（runs / arm / tmux_runs / launch / UI）不出现 `== "kimi"` 分支，provider 差异只住在 adapter 里。
2. 一个 attempt 的 trace_id 永远是真实 session id，没有占位，也没有别名。
3. 第一期已有的 claude 行为（sdk 与 tmux 两种 arm）在本期前后逐字节一致：argv、prompt、校验报错都不变。
4. provider 不支持的参数字段：UI 不显示，API 不接受（报 400，指明字段），不静默吞掉。例外是第一期已确立的“kimi 忽略 permission_mode / effort”，保持忽略。

## ACP run 生命周期（D6）

```
launching → running ──finish 上报──────────────────→ settled(done|failed by verify)
               │ ──turn 结束、无上报、end_turn────→ running（等 steer 或 finish）
               │ ──turn 结束、无上报、非 end_turn─→ settled(failed)
               │ ──通道关闭（Stop / 子进程退出）──→ settled(failed，除非已有 finish)
               └ one_shot：首 turn 结束 → 关闭通道 → 按上面的规则 settle
```

## 实测结论（kimi 2.1.1，2026-10-06，全部已验证）

| 问题 | 结论 |
|---|---|
| 会话固定/恢复 | **不能自选 id 新建**。`--session <id>`（简写 `-S`/`-r`）只恢复已存在会话，id 不存在直接报错。等价流程是两步：先建会话（`kimi -p` 首跑或交互首启），再从 `kimi session list --json` 拿 id 回灌 regin，之后 pane 用 `--session=<id>` 恢复。**tmux task run 按 D5 锚定：ACP `session/new` 建会话（id 即刻返回，无需 discover）→ pane 里 `--session=<id>` 恢复**。另注：commander 解析贪心，带值 flag 必须写成 `--flag=value` 且 `-p` 要放最后，否则值被吞 |
| Liveness 探针 | 有。`kimi session list --cwd <path> --json` 输出 id / workDir / sessionDir / updatedAt / lastTurnReason。注意 cwd 按 realpath 过滤（macOS `/tmp`≠`/private/tmp`），且只列已落盘索引 |
| tmux pane 交互 | 可用。`kimi --session=<id>` 在 pane 里正常出交互 UI；send-keys 驱动有效，但 **Enter 要发 C-m**（否则只进输入框不提交，tmux extended-keys off 时尤其明显）；会话状态栏直接显示 context 占用 |
| 非交互面 | `-p --output-format stream-json` 可用（meta/assistant/resume_hint 事件），但事件面薄，只配做一次性引导，不做驱动通道 |
| **SDK 级通道（ACP）** | **有，已端到端验证**。`kimi acp` 走标准 Agent Client Protocol（JSON-RPC over stdio）：initialize 能力协商 → `session/new` 立返 sessionId（**trace 锚定的确定性入口**，优于 create→discover）→ `session/prompt`（**必须带 id 的请求**，notification 形式会被静默丢弃——实测踩坑）→ 流式 `agent_message_chunk` → ack 带 `stopReason`。全量 surface：session load/resume/close/delete/fork/list、cancel、set_mode/set_model/set_config_option；反向 RPC 覆盖 tool 审批（`session/request_permission`）、fs 读写、terminal 执行。会话落同一 wire.jsonl，采集侧零改动。**这就是 claude sdk arm 的对等物**。npm 包无程序化导出（bin only）；`kimi web` 的 REST+WS API（bearer token，:58627）是另一条可编程面，但 ACP 更贴合 regin 的 subprocess 驱动模型 |
| workspace trust | 未信任目录首启会弹 Trust 交互窗，阻塞无人值守启动。信任落盘在 `~/.kimi-code/workspace-trust/wd_<dirname>_<sha256(path)[:12]>`（内容 `{"root":..., "trustedAt":...}`），regin 可预写。task run 面向已信任 repo 时不触发，worktree 新路径要处理 |
| wire.jsonl 位置 | `<sessionDir>/agents/main/wire.jsonl`，与采集侧已有解析一致；`sessionDir` 从 `session list --json` 直接拿到 |
| 权限/effort 词表 | 无 `--permission-mode/--effort` 等价物；最接近的是 `-y`（Ask When Needed）与 `--auto`（Never Ask）。kimi 的 arm 校验应对这些维度**跳过**而不是套用 claude 枚举。ACP 侧对应 `session/set_mode`：mode id 为 `default/plan/auto/yolo`，`yolo`≈`-y`、`auto`≈`--auto` |

## 源码级确认（kimi-code monorepo @ 21406fb4c，与 2.1.1 同代）

| 事项 | 结论 |
|---|---|
| hooks 在 ACP 下 | **照常触发**。externalHooks 是引擎级 feature，ACP server 以完整引擎启动、无 feature 排除、无 uiMode 门控；同一 TOML `[[hooks]]`、同一事件集（SessionStart/End、Pre/PostToolUse、UserPromptSubmit（带 isSteer）、Stop/Interrupt、SubagentStart/Stop 等）、cwd=会话 cwd。采集侧零改动成立 |
| 并发 prompt | 硬错误 JSON-RPC -32600 `code:"turn.agent_busy"`（acp-server/src/session.ts:631-638），**无队列、线上无 steer**（引擎 `agent.steer` 存在但 ACP 不调用）。regin 侧必须串行化：等 turn 结束或 `session/cancel` 后重发 |
| sessionId 铸造 | 引擎 `session_${randomUUID()}`（sessionLifecycleService.ts:903-905）；ACP 线层无 id 入参；klient 层 `CreateSessionOptions.sessionId?` 存在但未暴露到线上。无任何 KIMI_* env 可钉 id |
| 成本/用量 | `usage_update` 无成本数据；token 计量走 wire.jsonl 解析（采集侧已有） |
| Bash 执行位置 | client 声明 `terminal` 能力时 Bash 反 RPC 到 client；**regin 不声明该能力**，Bash 引擎侧执行、输出直接进 tool_call 卡片 |
| 子代理可见性 | ACP 流只挂 main agent 事件；子代理内部工具调用不在流上（子代理 spawn 表现为父 agent 的 Task tool_call）。不影响 trace：wire.jsonl + hooks 照常记录 |


## 验收（红→绿的测试名）

第一期（ACP arm，已于 2026-10-06 交付、全绿）：`test_arm_accepts_provider_kimi`、`test_arm_rejects_unknown_provider`、`test_kimi_arm_skips_claude_only_params`、`test_claude_arm_still_validates_its_vocabulary`、`test_kimi_acp_initialize_and_session_new_returns_id`、`test_kimi_acp_prompt_roundtrip_streams_chunks_and_stop_reason`、`test_kimi_acp_busy_prompt_rejected_turn_agent_busy`、`test_task_run_acp_launch_uses_provider_channel`、`test_against_real_kimi_smoke` / `test_active_provider_kimi_task_run_end_to_end`（真机测试，由 `REGIN_E2E_KIMI=1` 门控）。

第一期行为注记（仍然成立）：
- 无人值守：launch 后 `session/set_mode yolo`。审批 / 提问的反向 RPC 一律回 `cancelled`。
- `provider` 存在 attempt 的 `params_json` 里，无 schema 迁移。
- e2e 的 env overlay 必须把 ingest 指回真实服务器，因为 pytest 会把 `REGIN_INGEST_BASE_URL` 黑洞化。

本期的测试名列在下面切片表的 Tests 列，每片的 Verify 只跑它自己的测试文件。全量 pytest 只在 S10 跑一次。

### 切片表

公共约定：
- 所有 worktree 的 Verify 都以 `ln -sfn /Users/taowang/regin/.venv .venv` 开头。前端切片再链接 `frontend/node_modules`。
- 每片自己的 skill 会在任务内跑 scoped 测试，所以 Verify 不再重复 Delivers 以外的测试。

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | ACP run 生命周期 | `lib/agent_acp/launcher.py` + `lib/tasks/runs.py` + `lib/tasks/run_ending.py` + `lib/agent_sdk/registry.py`：按 D6 的状态机实现 settle；非 `end_turn` 记 failed；one-shot 首 turn 后关闭通道（`supervisor.end_after_turn` 加 ACP 分支，finish 上报后在当前 turn 结束时关闭）；arm 了 model 就走 ACP `session/set_model`；task images 作为 ACP image content block 发送（agent 未声明 image 能力时 RunRefused 并给出明确原因）；`session/new` 的 cwd 未给时取 `settings.project_root` 的绝对路径；`agent_runs` 行写入 cwd 与 model；测试放在 `tests/agent_acp/test_launcher_lifecycle.py` | D6, Invariant 2, ACP run 生命周期 | test_acp_non_end_turn_stop_reason_fails_attempt, test_acp_launch_turn_end_does_not_settle_without_finish, test_acp_finish_report_settles_attempt, test_acp_one_shot_closes_channel_after_turn, test_acp_arm_model_applied_via_set_model, test_acp_images_sent_as_content_blocks, test_acp_session_new_cwd_is_absolute |  | — | `f140/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/agent_acp tests/tasks/test_acp_launch.py |
| S2 | Provider 启动能力 | `AgentProvider.launch_capabilities()`（claude 声明 sdk+tmux 与三个字段；kimi 先只声明 sdk 与 model，tmux 由 S5 补上；codex / generic 返回空）；arm 按能力校验 provider、launch、字段（D1–D3），去掉 `arm.py` 里 "launch modes are Claude-only" 的字面量；`runs._channel_launcher` 对无通道的非 claude provider 抛 RunRefused；`/api/agent-runs/launch-options` 增加 `providers` 字段；doctor 增加 "task launch providers" 组（which + `--version`）；测试放在 `tests/tasks/test_launch_capabilities.py`，并扩展 `tests/tasks/test_arm_provider.py` | D1, D2, D3, Invariant 1, Invariant 4, 形状 | test_provider_declares_launch_capabilities, test_arm_rejects_provider_without_launch_channel, test_arm_rejects_launch_mode_provider_does_not_declare, test_arm_rejects_field_provider_does_not_declare, test_launch_options_lists_providers_with_availability, test_doctor_reports_task_launch_providers, test_claude_arm_unchanged |  | — | `f140/integration` | tdd | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/tasks/test_launch_capabilities.py tests/tasks/test_arm_provider.py |
| S3 | Kimi workspace 与 skill | `AgentProvider.prepare_workspace`（kimi 按 D8 预写 trust）在 channel launch 之前调用；`AgentProvider.skill_invocation`（D7，先用真机 ACP 探测 `/skill:<name>` 能否展开，结论写进本 feature spec 的 D7）；`lib/tasks/run_prompt.py` 改为通过 provider 生成 skill 段；`skill_catalog.available_skills(provider)` 与 `skill-options?provider=`；worktree 同步每个 provider 的 project dir（`.kimi-code/` 与 `.claude/` 一样处理）；测试放在 `tests/tasks/test_kimi_workspace.py` | D7, D8, Invariant 1, Invariant 3 | test_kimi_prepare_workspace_writes_trust_record, test_prepare_workspace_keeps_existing_record, test_channel_launch_prepares_workspace_before_session_new, test_skill_invocation_is_provider_owned, test_claude_skill_invocation_unchanged, test_skill_catalog_lists_provider_skills, test_worktree_syncs_provider_project_dir |  | S1 | `f140/integration` | goal-verified | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/tasks/test_kimi_workspace.py |
| S4 | Arm 面板选 provider | `useTaskArm` / `useArmPrefs` / `TaskArmSheet` / `SliceArmSheet`：从 launch-options 的 `providers` 渲染 provider 选择（不可用的项置灰并写明缺哪个二进制）；字段和 launch 选项按 `fields` / `launch_modes` / `labels` 显隐；body 带上 `provider`，不发 provider 未声明的字段；切换 provider 时丢弃记住的 model；skill 选择器带 `?provider=`；新增 e2e `frontend/tests/task-arm-provider.spec.js` | Invariant 4, 形状 | test_arm_sheet_provider_picker_lists_available_providers, test_kimi_arm_hides_claude_only_fields, test_kimi_arm_body_carries_provider_and_no_claude_fields, test_slice_arm_sheet_offers_provider, test_arm_prefs_drop_model_on_provider_switch, test_claude_arm_sheet_unchanged |  | S2 | `f140/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && cd frontend && npx vite build && REGIN_E2E_API_PORT=8351 REGIN_E2E_VITE_PORT=5191 REGIN_E2E_SCRATCH=/tmp/regin-e2e-f140s4 ./node_modules/.bin/playwright test task-arm-provider task-arm-launch |
| S5 | Kimi tmux arm | `KimiProvider.pane_argv` 与 claude 的 `claude_argv` 一起收进 adapter；`tmux_runs.launch_attempt` 按 provider 取 argv，并先按 D5 锚定 session id（先真机探测，再选路径）；prompt 经 pane delivery 发送，提交用 C-m；pane 守卫按 `pane_commands` 放行；`_binaries_missing` 按 provider 检查；kimi 在 `launch_capabilities` 中声明 tmux；测试放在 `tests/tasks/test_kimi_tmux_launch.py`，真机 e2e 由 `REGIN_E2E_KIMI=1` 门控，覆盖“启动→finish→settle”与“重启 serve 后 liveness 仍为 LIVE” | D4, D5, Invariant 1, Invariant 2, Invariant 3 | test_kimi_launch_argv_contains_no_claude_flags, test_task_run_tmux_launch_uses_provider_argv, test_pane_guard_allows_provider_declared_binaries, test_kimi_tmux_session_anchor_is_real_session_id, test_kimi_tmux_prompt_submitted_with_c_m, test_claude_tmux_argv_unchanged, test_real_kimi_tmux_run_settles_via_finish |  | S2, S3 | `f140/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/tasks/test_kimi_tmux_launch.py tests/agent_bridge |
| S6 | /live 与 Resume 按 provider | `/api/agent-runs` POST 接受 `provider`，kimi 走 `channel_launcher()`；`LiveLaunchSheet` 复用 S4 的 provider 选择；Resume 按会话自己的 provider 解析（`provider_for_trace`），不再用 `active_provider`；kimi 会话的 Resume 走 ACP `session/load` 接管；新增 e2e `frontend/tests/live-launch-provider.spec.js` | D2, Invariant 1, Invariant 2 | test_live_launch_routes_kimi_to_acp_channel, test_resume_uses_session_provider_not_active_provider, test_kimi_resume_loads_session_over_acp, test_live_launch_sheet_offers_provider |  | S1, S4 | `f140/integration` | goal-verified-treenav | ln -sfn /Users/taowang/regin/.venv .venv && ln -sfn /Users/taowang/regin/frontend/node_modules frontend/node_modules && .venv/bin/python -m pytest -q tests/web/test_live_launch_provider.py && cd frontend && npx vite build && REGIN_E2E_API_PORT=8352 REGIN_E2E_VITE_PORT=5192 REGIN_E2E_SCRATCH=/tmp/regin-e2e-f140s6 ./node_modules/.bin/playwright test live-launch-provider |
| S7 | 文档 | `ARCHITECTURE.md` 增加 provider 启动能力一节（launch_capabilities、通道与 pane 两种 arm、D5 锚定、D6 生命周期）；`README.md` 增加“用 Kimi 跑 task”：前置条件 `kimi login`、在 arm 面板选 kimi、选 ACP 或 tmux、怎么 attach tmux、已知限制；`CLAUDE.md` 与 `AGENTS.md` 只在有需要时改，两者保持逐字一致 | Intent, 非目标 | test_docs_name_every_launch_capability_field |  | S5, S6 | `f140/integration` | doc-hygiene | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python -m pytest -q tests/core/test_docs_launch_capabilities.py |
| S8 | integrate | 检查 S1–S7 都已合回 `f140/integration`，没有零 commit 的切片；合并冲突就地解决 | Invariant 1 | test_every_slice_merged_back | — | S7 | f140/integration | resolving-merge-conflicts | git merge-base --is-ancestor f140/s1 f140/integration && git merge-base --is-ancestor f140/s5 f140/integration && git merge-base --is-ancestor f140/s6 f140/integration && git merge-base --is-ancestor f140/s7 f140/integration |
| S9 | verify | 在 `f140/integration` 上跑一次全量 pytest（全 feature 唯一一次）；真机 kimi 用 ACP 与 tmux 各跑一个 task，直到 settle；跑 `regin migrate` 确认无 schema 落差（本期无 schema 变更）；失败就地修复 | Acceptance, Invariant 2, Invariant 3 | test_full_suite_passes, test_real_kimi_tmux_run_settles_via_finish, test_active_provider_kimi_task_run_end_to_end | — | S8 | f140/integration | run | ln -sfn /Users/taowang/regin/.venv .venv && .venv/bin/python cli/regin.py migrate && .venv/bin/python -m pytest -q -n auto --dist=loadfile && REGIN_E2E_KIMI=1 .venv/bin/python -m pytest -q tests/tasks/test_acp_launch.py tests/tasks/test_kimi_tmux_launch.py |







