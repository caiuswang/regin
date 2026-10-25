# F150 · sidebar 侧边栏优化

## Problem

当前侧边栏虽然已有分组，但存在以下问题：

- 高频入口（Pipeline、Inbox、Trace）没有排在最上方，扫描成本高。
- Live 使用频率低，却与 Inbox、Trace 同处 Observability 组顶部，视觉权重过高。
- 组与组之间的间距、标签样式不够统一，整体显得杂乱，缺乏成熟产品的质感。
- 移动端抽屉与桌面端 rail 的分组、顺序、图标状态需要保持一致。

## Goal

优化侧边栏，使其更符合成熟产品的标准：

- 高频入口触手可及，低频入口自然下沉。
- 分组精简、标签清晰、视觉层级一致。
- 保持现有的可访问性、键盘快捷键（`\` 折叠/展开）和响应式行为。
- 不新增页面、不改动现有 shell（Pipeline / Library / Trace）内部的 tab 结构。

## Design

### Shape

```ts
type NavLink = {
  to: string
  label: string
  icon: string
  exact?: boolean
  match?: string[]
  badge?: () => number
  tone?: () => 'info' | 'warning' | 'danger'
}

type NavGroup = {
  label: string
  links: NavLink[]
}
```

### Group reorganization

最终分组与顺序如下：

| 顺序 | 分组 | 入口 | 说明 |
|---|---|---|---|
| 1 | Workbench | Pipeline、Inbox | 每日最高频入口，置顶 |
| 2 | Observability | Trace、Memory、Audit、Live | Trace 仍保持 admin-only；Live 沉到组尾 |
| 3 | Library | Patterns & Skills、Repos、Prompts | 参考资产 |
| 4 | Engineering | Rules、Plans | 规则与计划 |
| 5 | Diagnostics | Schema drift、Payload log | 仍按 `diagEnabled` 条件显示 |
| 6 | System | Settings | 始终置于最底部 |

### Rules

#### Ordering
- 组内入口按使用频率从高到低排列。
- Pipeline 与 Inbox 必须位于 Workbench 组内，且 Inbox 紧随 Pipeline。
- Live 必须位于 Observability 组最后一位。
- Settings 必须位于最后一个分组的第一位（该组仅 Settings）。

#### Visual hierarchy
- 分组标签（section label）使用更克制的字重/字号，保持全大写但降低视觉噪音。
- 组间距统一：组内条目间距、组间间距、search 与 nav 间距使用同一套 token。
- 激活态条目保留高对比，但去掉过重的阴影，改用更 subtle 的背景+左侧指示条或当前渐变方案微调。
- 图标默认透明度保持 0.72，hover / active 时变为 1，维持当前行为。

#### Collapsed rail
- 折叠后仍只显示图标；悬浮预览卡（SidebarPreview）显示 group + label + badge。
- 折叠/展开快捷键 `\`、localStorage 持久化、移动端隐藏等现有行为不变。

#### Mobile drawer
- 抽屉内分组、顺序、图标、badge 与桌面 rail 完全一致。
- Quick search / New idea / theme toggle 保留在抽屉顶部/底部。

#### Accessibility
- 每个 nav item 保留 `aria-label`、`aria-current="page"`、键盘 focus outline。
- rail 折叠时，数字 badge 转为 dot，accessible name 仍通过 `aria-label` 读出数量与标签。

## Acceptance

### Functional

```gherkin
Given 用户已登录且 viewport >= 768px
When 打开任意内部页面
Then 侧边栏分组顺序应为 Workbench → Observability → Library → Engineering → Diagnostics（若启用）→ System

Given 用户在桌面端
When 查看 Workbench 组
Then 第一个入口是 Pipeline，第二个是 Inbox

Given 用户在桌面端
When 查看 Observability 组
Then Live 位于该组最后一位
```

### Visual

```gherkin
Given 用户在桌面端
When 侧边栏处于展开状态
Then 分组标签样式统一，组间距一致，无多余分割线

Given 用户 hover 任意 nav item
Then 图标 opacity 变为 1，背景色变化平滑

Given 用户点击当前 route 对应的 nav item
Then 该 item 显示激活态，且激活态不过度突出（无粗大投影或闪烁色）
```

### Responsive / rail

```gherkin
Given 用户在桌面端
When 按 `\` 键
Then 侧边栏在展开/折叠之间切换，且状态写入 localStorage

Given 侧边栏处于折叠状态
When 鼠标悬浮在 Inbox 图标上
Then 显示预览卡，包含 "Workbench"、"Inbox" 与当前未读数

Given viewport < 768px
When 点击顶部 hamburger
Then 抽屉打开，分组顺序与桌面 rail 一致
```

### Accessibility

```gherkin
Given 侧边栏处于折叠状态且 Inbox 有未读
Then 屏幕阅读器朗读 "Inbox (3)"

Given 用户使用 Tab 键遍历 nav items
Then 每个 item 都有可见的 focus outline
```

## Non-goals

- 不新增路由或页面。
- 不改 PipelineView、LibraryView、TraceView 内部的 tab 切换结构。
- 不修改 `/terminal/:id`、`/live/:id?` 等 chromeless / fixedViewport 的页面级行为。
- 不引入可自定义排序或拖拽功能。
