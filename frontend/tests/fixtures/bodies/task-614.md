## Delivers

frontend/src/components/editor/RichEditor.vue`（`@tiptap/vue-3`，`defineAsyncComponent` 按需加载），扩展：StarterKit 子集、Table、TaskList、Link、Placeholder、输入规则；`EditorToolbar.vue` 改为按 `mode` 分发到 textarea 命令或 Tiptap 命令，新增表格组；`MarkdownEditor.vue` 模式改为 rich/source，旧记忆值映射，窄屏规则删除。`FeatureSpec.vue` 与 `TaskBodyEditor.vue` 传 `previewTestid` 为 `feature-spec-rich` / `task-body-rich`。旧 E2E fixture 预置 source 模式。E2E `f176-rich.spec.js

## Spec 依据

Model 模式状态机, Rules 模式选择 / Rich 面输入（输入规则、快捷键、工具栏、表格）, Invariants 3 6 8

## 验收测试名

- `test_rich_is_default`
- `test_open_then_save_is_byte_exact`
- `test_table_edit_serializes_table`
- `test_markdown_input_rules`
- `test_mode_switch_is_lossless`
- `test_paste_markdown_text_parses`
- `test_legacy_specs_pass_in_source_mode`
- `test_rich_editor_is_lazy_chunk`
- `test_task_body_uses_rich`
- `test_defect_symptom_unchanged`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
