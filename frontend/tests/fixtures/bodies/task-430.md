## Delivers

lib/fulltext/__init__.py` + `session_docs.py` + `file_docs.py`: each kind ranks its full candidate set, and `_group` returns the exact `total` plus an `offset` slice. `web/blueprints/search.py` accepts `offset` (≥0) and keeps `MAX_LIMIT` per page. Span and file search inside a session are unchanged. Tests go in `tests/fulltext/test_search_paging.py

## Spec 依据

R2, Invariants, Today's server

## 验收测试名

- `test_session_total_is_not_capped_at_twice_the_limit`
- `test_offset_pages_without_overlap`
- `test_last_page_reports_end`
- `test_span_scope_unchanged`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
