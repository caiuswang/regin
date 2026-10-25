## Delivers

lib/files/sources.py` (parse a `FileSource` from request args: project+path+rev, session+path, session+span; the root/path split of D11a); `lib/files/confine.py` (R1: realpath containment against `repos.path` or `roots.session_cwd`, the span-named grant via the trace's file-touching spans, role gates editor for project and admin for session/span); `lib/files/classify.py` (R2, the one classifier, I6: extension table, NUL sniff in the first 8 KiB, UTF-8 check, size caps of 2 MiB text and 50 MiB media); `lib/files/reader.py` (worktree bytes, `git show <rev>:<path>` for HEAD, span-captured Read content with `first_line` = the Read offset, `diffable`); `web/blueprints/files.py` with GET /api/files/meta and GET /api/files/raw (nosniff, `Content-Security-Policy: sandbox`, `download=1` attachment), registered in web/app.py; `tests/files/` covering every backend scenario; no UI, no write path (I3)

## Spec 依据

D5, D6, D7, D11, Model, R1, R2, I1, I3, I5, I6

## 验收测试名

- `test_project_path_escaping_the_repo_is_refused`
- `test_symlink_inside_the_repo_pointing_outside_is_refused`
- `test_viewer_role_cannot_read_project_files`
- `test_session_source_serves_a_span_named_path_outside_the_cwd`
- `test_session_source_refuses_an_unnamed_path_outside_the_cwd`
- `test_non_admin_cannot_read_session_or_span_sources`
- `test_classification_follows_r2`
- `test_raw_responses_carry_nosniff_and_a_sandbox_csp`
- `test_head_rev_serves_committed_bytes_while_the_worktree_differs`
- `test_missing_file_returns_missing`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
