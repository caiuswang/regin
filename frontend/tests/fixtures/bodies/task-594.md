## Delivers

新增 `frontend/src/components/editor/MarkdownEditor.vue`：props `modelValue/project/feature/images/attach`，emit `update:modelValue` 原样上抛；内含 `RefTextarea`（源码面，textarea 保留宿主传入的 `data-testid`、`aria-label`、`@paste`/`@drop` 监听与 `ref`→`textarea` 暴露，供 `utils/caret.js` 读光标）与 `MarkdownContent`（预览面，`refs` 开、`project` 透传，输入 `resolveImageRefs(modelValue, images)`，每次输入同步重算，无防抖）；模式切换条「源码 / 分屏 / 预览」三个 `aria-pressed` 按钮，模式状态机按 spec：宽屏无记忆→split，记忆读写 `localStorage` key `regin_editor_mode`，窄屏（< 768px，`matchMedia`）强制 source 且不覆盖记忆；工具栏位置预留插槽但本片不实现按钮。`FeatureSpec.vue` 用它替换现有 `RefTextarea + v-show 预览` 组合，删除 `previewing` 状态与 Preview/Source 切换按钮（模式切换归组件），`fs-area`/`fs-body` 样式迁入组件或经 class 透传；预览面保留 `data-testid="feature-spec-preview-pane"`。宿主 `onPaste/onDrop/takeImages/save` 不变。E2E `frontend/tests/f174-split-editor.spec.js` 覆盖本片 5 个 Scenario；`feature-images.spec.js`、`ref-mentions.spec.js` 继续通过

## Spec 依据

Model, Rules 编辑器模式选择, Rules 预览面, Invariants 1 2 6 7

## 验收测试名

- `test_split_default_on_wide_viewport`
- `test_typing_renders_preview`
- `test_mode_is_remembered`
- `test_narrow_viewport_falls_back_to_source`
- `test_save_is_byte_exact`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
