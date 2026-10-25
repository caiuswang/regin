## Delivers

在 S1 组件上验证并补齐：粘贴/拖拽图片走宿主 `attach()`，成功后 `withCaretInsert` 写 `![](image:N)` 且预览面立刻出现对应 object URL 的 `<img>`（`images` prop 变化即重算）；`attach()` 抛错或返回空时源码字节不变、宿主 `fs-error` 显示错误（`FeatureSpec.takeImages` 补 try/catch 并写 `error`，今天抛错会无提示）；`@` 菜单在 split 模式下的 `Teleport` 定位仍以 textarea 的 caret 为锚（`RefTextarea.anchorFor` 不受左右分栏影响），接受后预览面渲染 `RefChip`；预览面不可编辑（无 `contenteditable`，点击 chip 仍导航）。E2E `frontend/tests/f174-images-refs.spec.js` 覆盖本片 3 个 Scenario，复用 `feature-images.spec.js` 的 clipboard 构造与 `ref-mentions.spec.js` 的 `/api/refs` mock

## Spec 依据

Model 图片生命周期, Rules 引用与图片语法保持, Invariants 4 5

## 验收测试名

- `test_pasted_image_writes_reference_and_previews`
- `test_failed_upload_leaves_source_untouched`
- `test_mention_contract_preserved`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
