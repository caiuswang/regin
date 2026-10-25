## Delivers

RefTextarea.vue` 经 `defineExpose` 暴露 `replaceRange(start, end, text)` 与 `setSelection(start, end)`（现有 `execCommand('insertText')` 路径，失败回退 `setRangeText` + 派发 `input`）；新增 `frontend/src/components/editor/EditorToolbar.vue`，按 Rules「工具栏」表实现粗体/斜体/标题/无序列表/有序列表/复选框/代码块/链接八个按钮：包裹类（粗体、斜体、代码块）按有/无选区分支并把光标放回正确位置；行级类（标题、三种列表）对选区覆盖的每一行行首插入，标题在已有 `#` 前缀上加一级、上限 `####`；链接按钮用 `reka-ui` Popover 内联输入 url（不用 `window.prompt`），回车生成 `[label](url)`，Esc 取消不改源码。每个按钮一次 `replaceRange` 调用，使 `Cmd/Ctrl+Z` 一次撤销到按下前的文本与选区；按钮带 `aria-label` 与 `title`，仅在 source/split 模式渲染。工具栏挂进 S1 预留的插槽，位于源码区上方。E2E `frontend/tests/f174-toolbar.spec.js` 覆盖本片 3 个 Scenario，撤销用 `page.keyboard.press('ControlOrMeta+z')` 断言

## Spec 依据

Rules 工具栏, Invariants 1 3

## 验收测试名

- `test_toolbar_bold_is_one_undo_step`
- `test_toolbar_line_marks_apply_per_line`
- `test_toolbar_link_prompts_for_url`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
