## Delivers

lib/refs/resolve.py` `file_item` sets `url = /files?project=<id>&path=<key>` when the project is known, null otherwise (D10), so `RefChip.vue` renders a link; `DiffCard.vue` filename opens `FilePreviewDrawer` with a session source; the Read row (`InlineToolRow` and the reader view) gains a View action opening a span source; `SearchFilePreview.vue` gains Open file, opening a session source at the hit line; the drawer is mounted once in the trace view; `tests/refs/` case for the file url; `frontend/tests/file-entry.spec.js

## Spec 依据

D8, D10, R4

## 验收测试名

- `test_a_file_reference_with_a_project_resolves_to_a_viewer_url`
- `test_file_reference_chip_opens_the_viewer`
- `test_diffcard_filename_opens_the_current_file_in_the_drawer`
- `test_read_span_opens_captured_content_at_its_offset`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
