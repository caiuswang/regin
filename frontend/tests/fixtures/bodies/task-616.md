## Delivers

两面都无内滚：`RefTextarea` 加 `useAutogrow`（input 事件驱动），rich 面 `min-height` 而无 `max-height`；工具栏 + Save/Cancel 迁入组件头部并 `position: sticky` 于 `.content-scroll`；`Layout` 状态与 Expand/Collapse/Esc/⌘⇧F；focus 时宿主经 `provide/inject` 的 `editorFocus` 隐去其它卡片与 section nav，标题行显示 `title`；进入/退出保持当前块可见；Cancel 与路由离开时 dirty 确认（`useConfirm`，`onBeforeRouteLeave`）。用 `node scripts/dom-measure.mjs --route /features/<id> --explain` 核对 sticky 与无内滚。E2E `f176-layout.spec.js

## Spec 依据

Model 版式状态机, Rules 长文版式 / 离开保护, Invariants 7

## 验收测试名

- `test_inline_grows_and_toolbar_sticks`
- `test_source_textarea_autogrows`
- `test_focus_mode`
- `test_cancel_confirms_when_dirty`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
