# F164 新建打包版本 — 发布 regin v0.4.0

## 意图
自 `v0.3.0`（2026-07-13）以来 master 已累积 787 个提交（feature pipeline、task runs、Kimi、defect ledger 等），但没有新版本。本 feature 切出 **v0.4.0**：先刷新过时的 README 与相关文档，再统一版本号、补齐 CHANGELOG（含缺失的 0.3.0 段）、打 annotated tag，并发布 GitHub Release。

现状缺口（本次 spec 时实测）：
| 位点 | 现值 | 问题 |
|---|---|---|
| `pyproject.toml` `version` | `0.2.0` | 落后两个版本（v0.3.0 发布时未改） |
| `regin-plugin/plugins/regin-agents/.claude-plugin/plugin.json` `version` | `0.2.0` | 同上 |
| `CHANGELOG.md` | 最新段为 `[0.2.0]` | 缺 `[0.3.0]`，也无 `[0.4.0]` |
| git tag / GitHub Release | 最新 `v0.3.0` | 无 v0.4.0 |
| README / ARCHITECTURE.md / docs/setup.md / docs/agents/issue-tracker.md / docs/defect-task-feature.md | — | 缺 CodeBuddy/WorkBuddy、projects/workspaces、任务输出语言、`regin` MCP pipeline 工具；引用已删除的 `GradesView.vue`、`lib/config.py`、`regin task ship`；后两份文档仍描述已退役的 `feature_key` 字符串模型 |

## 非目标
- 不做 PyPI / npm 发布，不构建 wheel、不产出 zip 包；"打包版本"= git tag + GitHub Release。
- 不改 `frontend/package.json`（`0.0.0`，私有、不随版本发布）、`website/` 内容（例外：I5 门禁所需的 D321 修复——`website/e2e/fidelity.spec.js` 位点重钉，`website/src` 不动）。
- 不新增发布脚本 / CI 自动化；沿用 v0.2.0 的手工流程（一个 `release:` 提交 + annotated tag）。
- 不重写 README 结构，只补缺、改错。

## 形状

```ts
type Release = {
  version: "0.4.0"                 // 0.x 阶段 minor 递增；破坏性变更允许
  tag: "v0.4.0"                    // annotated, tagger message = "regin v0.4.0 — <headline>"
  target: Commit                   // 本 feature 的 release 提交（不是任意 HEAD）
  versionSites: ["pyproject.toml", "regin-plugin/plugins/regin-agents/.claude-plugin/plugin.json"]
  changelog: { "0.4.0": Section, "0.3.0": Section }   // 0.3.0 段由现有 GitHub Release v0.3.0 正文回填
  ghRelease: { title: "v0.4.0 — <headline>", notes: Markdown, latest: true }
}
type Section = { date: ISODate, headline: string, added: string[], changed: string[], fixed: string[] }
```

## 规则

### 发布状态机
```
stale-docs ──(文档刷新提交 "docs: …"，先于 release 提交落到 master)──▶ draft
draft ──(版本位点+CHANGELOG 提交为 "release: v0.4.0")──▶ committed
committed ──(git tag -a v0.4.0 <release commit>)──▶ tagged
tagged ──(git push origin master v0.4.0)──▶ pushed
pushed ──(gh release create v0.4.0 --notes-file …)──▶ published
```
任何一步失败停在当前状态并报告，不回滚已推送的 tag。

### 决策表：CHANGELOG 内容来源
| 段 | 来源 | 时间范围 |
|---|---|---|
| `[0.3.0] — 2026-07-13` | `gh release view v0.3.0` 正文，改写为 Keep-a-Changelog 结构 | v0.2.0..v0.3.0 |
| `[0.4.0] — <发布当天>` | `git log v0.3.0..<release commit>` 按主题归类（feat/fix/F### 提交） | v0.3.0..release |

### 决策表：文档刷新（范围 = README、ARCHITECTURE.md、docs/setup.md、CONTEXT.md、PRODUCT.md、docs/agents/、docs/topics/、docs/trace/、docs/defect-task-feature.md）
| 条件 | 动作 |
|---|---|
| 文档引用的文件路径 / `regin` 子命令 / SPA 路由 / `settings.*` 键已不存在 | 改成现行名称，或删去该句 |
| v0.3.0 之后 ship 的功能在 README 四层（Workbench/Runs/Observability/Guidance）与 Supported agents 表中无任何提及 | 在对应小节补 1–3 行 |
| 整份文档描述的是已退役模型，且仍被 README / skill 链接 | 按现行 schema 重写 |
| 设计/调研类文档中提到的未来路由（如 `/trace/tool-usage`） | 不改 |
| `docs/setup.md` CLI 参考段 | 由 `scripts/gen_cli_reference.py` 生成；重跑后无 diff 即为最新 |

### 不变量
- I1 所有版本位点值相等，且等于 tag 去掉 `v` 前缀。
- I2 `v0.4.0` 指向的提交包含 I1 成立的版本位点和 `[0.4.0]` CHANGELOG 段。
- I3 CHANGELOG 段按版本降序：`0.4.0` > `0.3.0` > `0.2.0` > `0.1.0`。
- I4 GitHub Release 正文末尾为 `Full changelog: https://github.com/caiuswang/regin/compare/v0.3.0...v0.4.0`，开头注明提交数（与 `git rev-list --count v0.3.0..v0.4.0` 一致）。
- I6 文档刷新提交是 release 提交的祖先。
- I5 发布前全量测试通过（`.venv/bin/python -m pytest -q -n auto --dist=loadfile`）且前端可构建（`npx vite build`）；不通过则停在 `draft`。

## 验收

```gherkin
Scenario: 版本位点一致
  When 运行 grep '^version' pyproject.toml 与 jq -r .version regin-plugin/plugins/regin-agents/.claude-plugin/plugin.json
  Then 两者都输出 0.4.0

Scenario: tag 是 annotated 且指向 release 提交
  When 运行 git cat-file -t v0.4.0
  Then 输出 tag
  And git log -1 --format=%s v0.4.0 输出 "release: v0.4.0"
  And git show v0.4.0:pyproject.toml 含 version = "0.4.0"

Scenario: CHANGELOG 补齐
  When 运行 grep -n '^## \[' CHANGELOG.md
  Then 依次为 [0.4.0]、[0.3.0]、[0.2.0]、[0.1.0]

Scenario: GitHub Release 已发布且为 Latest
  When 运行 gh release list
  Then 第一行是 v0.4.0 且标记 Latest
  And gh release view v0.4.0 正文末行含 compare/v0.3.0...v0.4.0

Scenario: 文档引用无失效
  When 对刷新范围内的文档抽取反引号中的路径、`regin <cmd> [<sub>]`、SPA 路由、`settings.*` 键并逐一核对
  Then 除模块限定名（如 `lib/tokens/model_windows.infer_window`）与设计文档中的未来路由外，没有失效项
  And `.venv/bin/python scripts/gen_cli_reference.py` 之后 `git diff docs/setup.md` 为空

Scenario: 新功能有文档入口
  When grep README.md
  Then CodeBuddy、WorkBuddy、workspace、`regin mcp register` 各至少出现一次

Scenario: 发布门禁
  Given 全量 pytest 或 vite build 失败
  Then 不创建 tag，不推送，任务以 failed 报告失败项
```

