# F172 Task 运行的输出语言 + 启动提示词预览

## 意图

所有 task run（默认的 spec / build 阶段、自定义 task、defect 生成的 fix task）都经过同一个入口
`lib/tasks/run_prompt._prompt()` 组装启动提示词。目前提示词里没有说明输出语言，agent 用中文还是英文
回答全看运气。本功能做两件事：

1. **输出语言**：每个项目可以配置一个输出语言，另有一个全局默认值。这两个都没设置时，提示词和现在逐字节一致。
2. **提示词预览**：打开 task 或准备运行 task 时，直接显示这次 run 将会收到的完整启动提示词，
   不用等到开始运行才看到。

非目标：
- 只对 task run 生效；triage、drafting、grader 等其他 prompt surface 不加语言块。
- 不支持单次 attempt 的语言覆盖（arm 参数里不加 `language`），以后需要再做。
- 不翻译 regin 自己的 prompt surface 文案，surface 仍是英文，只是告诉 agent 用什么语言输出。
- 预览展示的是"现在启动会说什么"，不保存历史 run 实际发出的提示词，那份在 trace 的首条 prompt span 里。

## 形状

```ts
// 存储
repos.output_language   : TEXT NULL        // 项目级；NULL/"" = 未设置
settings.tasks.output_language : str = ""  // 全局默认（TasksConfig）

// 取值：自由文本，trim 后 ≤ 64 字符，例如 "简体中文" / "English" / "中文，代码与命令保持英文"
type OutputLanguage = string               // "" 表示未设置

// 新 prompt surface（lib/prompts/surfaces/tasks.py，area="task-run"）
LANGUAGE_SURFACE_ID = "task-run-language"  // 变量: {{language}}
// 启动信封 task-run-launch 增加 {{language}}，位置在 {{finish}} 之后
_LAUNCH_BODY = "{{invocation}}{{body}}{{tracker}}{{followup}}{{feature}}{{spec}}{{references}}{{finish}}{{language}}"

// 预览接口
GET /api/tasks/<id>/prompt-preview  (require_auth)
  → 200 { prompt: string, language: string, language_source: "project"|"default"|"none", run_id_placeholder: boolean }
  → 404 未知 task
```

`task-run-language` 默认正文的意思（英文落地）：*用 {{language}} 写所有给人看的文字，包括回复、
send_to_user 消息、写出的 spec 或报告、task 评论，以及 finish 命令的 `--reason`；代码、标识符、命令、
文件路径和引用的输出保持原样。*

## 规则

### 语言解析（`_language_block(task, feature)`）

| 任务所属项目 `_project_of(task, feature)` | `repos.output_language` | `settings.tasks.output_language` | 结果 | `language_source` |
|---|---|---|---|---|
| 有 | 非空 | 任意 | 用项目值渲染 `task-run-language` | project |
| 有 | 空/NULL | 非空 | 用全局值渲染 | default |
| 无 | — | 非空 | 用全局值渲染 | default |
| 任意 | 空 | 空 | `""`（不生成块） | none |

### 预览（`GET /api/tasks/<id>/prompt-preview`）

| task 状态 | 用到的 params | finish 块里的 run id |
|---|---|---|
| armed（有待运行的 attempt 行） | 该行的 `params`（skill invocation、worktree base 都会体现） | 该 armed 行的真实 id |
| 其他（idea / done / failed / running …） | `None` | 占位符 `<run id>`，`run_id_placeholder=true` |

### 设置入口

| 页面 | 编辑的字段 | 写入方式 |
|---|---|---|
| `/projects/<id>`（ProjectStagesView） | `repos.output_language` | 新接口 `PATCH /api/projects/<id>/output-language`，`require_editor` |
| `/projects/default` | `settings.tasks.output_language` | 现有的 settings 写入路径；SettingsView 的 Tasks 区块也要同步显示这个字段 |

### 展示位置

| 界面 | 展示内容 |
|---|---|
| TaskDetailView（打开 task） | 可折叠的 "Launch prompt" 区块，默认收起，展开时请求预览 |
| TaskArmSheet（arm/运行前） | 同样的预览，基于当前已 arm 的参数；重新 arm 后刷新 |
| 两处共同 | 显示语言及来源标签（项目 / 默认 / 未设置）；请求失败时显示错误，不能留白 |

## 不变量

- I1：`settings.tasks.output_language` 和项目值都为空时，`_prompt()` 的输出与本功能上线前逐字节相同。
- I2：语言块拼在 `PROMPT_MAX` 截断**之后**，正文再长也不会把它挤掉。
- I3：预览**没有副作用**：不建 run 行、不建 worktree、不改 task 状态、不触发依赖。
  预览和真实启动调用的是同一个 `_prompt()`，两者的唯一差别是 run id（非 armed 时用占位符）。
- I4：信封被改成不含 `{{language}}` 时，语言块就不出现，这是操作者的选择。只有 `{{finish}}` 会被补回。
- I5：这是 schema 变更，三处要一起改：新的 `alembic/versions/00NN_*.py`、`db/schema.sql`、`Repo` 模型字段。
- I6：修改信封默认正文之前，要把**当前**正文的 sha256 用 `register_retired_default(LAUNCH_SURFACE_ID, …)` 登记，
  这样已有数据库里没被用户编辑过的信封行才会更新为新默认正文。用户编辑过的信封保持不动。

## 验收

```gherkin
Feature: Task run 输出语言与提示词预览

  Scenario: 两个值都未设置时提示词不变
    Given settings.tasks.output_language 为 "" 且 task 所属项目的 output_language 为 NULL
    When 组装该 task 的启动提示词
    Then 输出与本功能上线前的 _prompt() 输出逐字节相同

  Scenario: 项目值优先于全局默认
    Given 全局默认为 "English" 且项目 output_language 为 "简体中文"
    When 组装该项目下某个 build task 的启动提示词
    Then 提示词包含渲染后的 task-run-language 块，且其中含 "简体中文"
    And 不含 "English" 作为语言值

  Scenario: 项目未设置时回落到全局默认
    Given 全局默认为 "简体中文" 且项目 output_language 为 NULL
    When 组装 spec task、自定义 task、defect fix task 各自的启动提示词
    Then 三份提示词都包含 task-run-language 块，且其中含 "简体中文"

  Scenario: 正文超长时语言块仍在
    Given 项目 output_language 为 "简体中文"，且 task body 长度为 PROMPT_MAX + 500
    When 组装启动提示词
    Then 语言块和 finish 块都完整出现在提示词末尾

  Scenario: 已有数据库里未编辑过的信封会更新
    Given prompt_templates 中 task-run-launch 行的正文等于旧默认正文
    When 执行 seed_builtin_skeletons()
    Then 该行正文变为包含 {{language}} 的新默认正文
    And 被用户编辑过的信封行保持不变

  Scenario: armed task 的预览与真实启动一致
    Given 一个已 arm（带 skill 和 worktree base）的 task，armed attempt 行 id 为 R
    When GET /api/tasks/<id>/prompt-preview
    Then 返回的 prompt 等于 _prompt(task, R, 该行 params)
    And run_id_placeholder 为 false
    And 前后 agent_task_runs 行数、task 状态不变，也没有新建 worktree

  Scenario: 未 arm 的 task 预览用占位符
    Given 一个状态为 idea 的 task
    When GET /api/tasks/<id>/prompt-preview
    Then 200，prompt 的 finish 命令中 run id 为 "<run id>"，run_id_placeholder 为 true

  Scenario: 未知 task
    When GET /api/tasks/999999/prompt-preview
    Then 404

  Scenario: 在项目页设置语言
    Given 以 editor 身份打开 /projects/<id>
    When 把输出语言设为 "简体中文" 并保存
    Then repos.output_language 为 "简体中文"
    And 该项目下 task 的预览显示语言 "简体中文"，来源为 "项目"
    When 清空并保存
    Then repos.output_language 为 NULL，预览来源回落为 "默认" 或 "未设置"

  Scenario: 输入超长被拒绝
    When PATCH /api/projects/<id>/output-language，值为 65 个字符
    Then 400，且错误信息指出字段 output_language

  Scenario: 打开 task 时能看到提示词
    Given 打开某个 task 的 TaskDetailView
    When 展开 "Launch prompt"
    Then 显示的文本与 prompt-preview 接口返回的 prompt 一致
    And 预览接口出错时显示错误信息，而不是空白区块
```
