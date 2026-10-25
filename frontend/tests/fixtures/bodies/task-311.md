## Delivers

lib/refs/grammar.py` (parse_refs skipping fenced and inline code, format_ref with the label escapes, file-key encode/decode, kind aliases); `lib/refs/search.py` (per-kind search reusing each list endpoint's q filter, ranking M2–M4, Recent per R9, file kind via git ls-files M5, Session group admin-only); `lib/refs/resolve.py` (resolve_refs with project_id and can_read_sessions, RefItem incl. feature_id, project_id, restricted, exists); `web/blueprints/refs.py` with GET /api/refs (@require_editor) and GET /api/refs/resolve (@require_auth, ≤100), registered in web/app.py; `frontend/src/utils/refs.js` (the same grammar, formatRef, refEndingAt for E3); `tests/fixtures/refs_grammar_cases.json` with the 18 Gates cases; no UI

## Spec 依据

D1, D3, D7, D10, Model, R2, R9, I1, I3, I4, I6, I7, I12

## 验收测试名

- `test_python_grammar_parses_every_fixture_case`
- `test_javascript_grammar_parses_every_fixture_case`
- `test_format_ref_escapes_markup_in_labels`
- `test_search_groups_by_kind_with_exact_id_first`
- `test_search_kind_filter_and_aliases`
- `test_search_recent_is_scoped_to_the_selected_project`
- `test_search_ranks_selected_project_first`
- `test_search_file_kind_lists_tracked_repo_files_only`
- `test_search_file_query_cannot_leave_repo`
- `test_search_requires_editor_and_sessions_need_admin`
- `test_resolve_returns_live_item_or_null`
- `test_resolve_restricts_sessions_to_admins`
- `test_resolve_refuses_malformed_or_too_many_refs`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
