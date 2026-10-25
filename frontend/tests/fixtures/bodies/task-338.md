## Delivers

monaco-editor` added to frontend/package.json; `frontend/src/components/files/FileViewer.vue` (fetches meta, then dispatches on `preview` through one registry `frontend/src/components/files/registry.js`, I6/I7; lifecycle axis A with loading, ready, download_only, denied, missing, failed); `FileCodeView.vue` (Monaco read-only via dynamic import, `editor.worker` only, D1/D2; language from meta; line numbers from `first_line`; `#L10-L20` anchors reveal and decorate); `FileMediaView.vue` (image with fit/1:1, svg via `<img>`, pdf in an iframe from a blob URL, audio, video, all via `api.getBlobUrl`); `FileBinaryCard.vue` (name, size, mime, Download; also used for too_large); `frontend/src/views/FilesView.vue` at route `/files` with its empty, collapsed left pane (D11b) and all state read from the query and hash (I8); `FilePreviewDrawer.vue` mounting the same viewer (D8); `frontend/tests/file-viewer.spec.js

## Spec 依据

D1, D2, D5, D8, D11, Model, R2, I4, I6, I7, I8

## 验收测试名

- `test_code_opens_read_only_in_monaco_with_highlighting`
- `test_monaco_is_not_loaded_until_a_code_view_mounts`
- `test_line_anchor_reveals_and_highlights_lines`
- `test_svg_scripts_do_not_run`
- `test_pdf_renders_in_the_native_viewer`
- `test_binary_file_offers_only_metadata_and_download`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
