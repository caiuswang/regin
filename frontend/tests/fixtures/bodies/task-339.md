## Delivers

dompurify` and `papaparse` added to frontend/package.json; `FileMarkdownView.vue` (`marked` then DOMPurify then `v-html`, I2; relative links navigate the viewer within the same root; relative images fetched as blob URLs through the same source, refused paths not fetched); `FileHtmlView.vue` (`<iframe sandbox srcdoc>`, scripts off by default, the Run scripts toggle adds only `allow-scripts`, never `allow-same-origin`, reset on source change, D4); `FileCsvView.vue` (Papa Parse, first 1,000 rows, truncation notice); `FileNotebookView.vue` (nbformat 4: markdown cells via the markdown view, code cells via `monaco.editor.colorize`, `text/plain`, `image/png`, `image/jpeg` and sandboxed `text/html` outputs); Rendered/Source toggle (axis B) in the toolbar; registry rows for markdown, html, csv, notebook; `frontend/tests/file-render.spec.js

## Spec 依据

D3, D4, R2, R3, R4, I2, I7

## 验收测试名

- `test_html_preview_runs_no_script_by_default`
- `test_html_scripts_toggle_runs_scripts_in_an_opaque_origin`
- `test_markdown_is_sanitized`
- `test_relative_image_in_markdown_loads_from_the_same_source`
- `test_notebook_renders_cells_and_image_outputs`
- `test_csv_renders_as_a_truncated_table_with_a_source_toggle`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
