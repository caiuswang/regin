## Delivers

ref` 节点 NodeView 渲染 `RefChip`（编辑态不导航）+ `@tiptap/suggestion` 接 `useRefMentions` 与 `RefMenu`；内部 URL 粘贴转 ref；`image` 节点 NodeView 经 `images` 解析，粘贴/拖拽经宿主 `attach()`，失败不插入；`opaque` NodeView：只读卡片 + 双击就地源码框。E2E `f176-refs-images.spec.js

## Spec 依据

Rules Rich 面输入（@ / 粘贴 / 图片 / opaque）, Invariants 4 5

## 验收测试名

- `test_mention_in_rich`
- `test_paste_image_in_rich`
- `test_failed_upload_in_rich_leaves_doc_untouched`
- `test_opaque_block_roundtrip_and_edit`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
