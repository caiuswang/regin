## Delivers

frontend/src/editor/markdownDoc.js`：`parse(src) → { doc, segments, retained }`（marked lexer，Rules「解析」表，含 opaque/ref/image 节点的 schema 定义 `frontend/src/editor/schema.js`）与 `serialize(doc, segments, retained) → string`（Rules「序列化」表，`@tiptap/pm/markdown` 的 `MarkdownSerializer` 加 table/ref/image/opaque 规则）。`scripts/export-bodies.py` 从 DB 导出全部 features.body 与 agent_tasks.body 到 `frontend/tests/fixtures/bodies/`；`frontend/tests/unit/markdownDoc.test.mjs` 对全部语料断言恒等，并对 Rules 表每一行各一例。新增依赖：`@tiptap/core @tiptap/pm`（仅 schema 与 serializer，无 UI）

## Spec 依据

Model 块级源码保留, Rules 解析/序列化, Invariants 1 2 4 5

## 验收测试名

- `test_roundtrip_corpus_is_identity`
- `test_edit_rewrites_only_touched_block（单测版）`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
