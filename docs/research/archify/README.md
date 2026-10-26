# Archify 试画：regin pipeline + 架构图

用 [Archify](https://github.com/tt-a1i/archify) v3.2.0（commit `81c6be8`，Apache-2.0）给 regin 画的两张样图，用来评估它能否作为可选的第三方渲染器。

| 图 | 源（Archify JSON IR） | 交互版 | 截图 |
|---|---|---|---|
| Feature pipeline（workflow） | `regin-pipeline.workflow.json` | `regin-pipeline.html` | `regin-pipeline.{dark,light}.png` |
| 系统架构（architecture） | `regin-architecture.architecture.json` | `regin-architecture.html` | `regin-architecture.{dark,light}.png` |

HTML 是自包含的单文件（约 850 KB，内含 viewer），直接用浏览器打开：支持聚焦、上下游、路径追踪、明暗主题，以及导出 PNG/SVG。

## 重新生成

```bash
git clone --depth 1 https://github.com/tt-a1i/archify /tmp/archify
cd docs/research/archify
ARCHIFY_UPDATE_CHECK_DISABLED=1 node /tmp/archify/archify/bin/archify.mjs \
  finalize workflow regin-pipeline.workflow.json regin-pipeline.html --quality showcase --json
```

`finalize` 会依次跑完 validate → deliver → check → browser-check（真实 Chrome）四道 gate。这两张图第一次提交就全部通过；architecture 图只给了一条建议性提示：`mcp → regin_db` 这条线需要拐两次弯。

## 观察

| 维度 | 结论 |
|---|---|
| 输入 | 带类型的 JSON IR（每种图一个 schema），由 agent 或代码直接生成；不解析 Mermaid |
| 布局 | workflow 只需给 lane + col（0–5），坐标由编译器算；architecture 要手给 `pos`/`size`，可读性取决于摆放 |
| 列上限 | workflow 最多 6 列，pipeline 的阶段数如果超过 6，必须合并列 |
| 运行时 | Node ≥ 18，CLI 零依赖；browser-check 需要本机装有 Chrome |
| 网络 | 只有可选的更新检查，用 `ARCHIFY_UPDATE_CHECK_DISABLED=1` 关掉 |

## 接入建议（不做强依赖）

TaskGraph 的节点和边来自 `agent_tasks` + `agent_task_deps`，状态是活的，适合继续由 SPA 原生渲染。Archify 适合用在**导出 / 分享快照**这类场景：regin 从 DB 生成 workflow JSON，如果本机能找到 `archify` 就调用 `finalize` 产出 HTML，找不到就隐藏这个入口。具体做法是在 `settings` 里加一个可选的 `archify_bin`，`regin doctor` 报告是否可用，失败时不影响其它功能。
