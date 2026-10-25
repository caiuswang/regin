## Delivers

Toolbar actions per R3: Copy path, Copy content, Download, Copy link for the current selection; Changes vs HEAD shown only when `diffable`, opening the Monaco diff editor (HEAD raw vs WORKTREE raw) with side-by-side/inline toggle; WORKTREE/HEAD switch for git roots; Current on disk for span sources; Open in VS Code (`vscode://file/<abs>:<line>`) shown only on a loopback hostname (D9); view mode, rev and anchor written to the URL so a reload reproduces the state (I8); `frontend/tests/file-actions.spec.js

## Spec 依据

D9, D11, R3, I3, I8

## 验收测试名

- `test_changes_vs_head_shows_a_monaco_diff`
- `test_open_in_vs_code_shows_only_on_a_loopback_host`
- `test_files_page_state_is_fully_addressed_by_its_url`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
