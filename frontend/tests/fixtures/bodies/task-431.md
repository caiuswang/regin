## Delivers

New `lib/fulltext/entity_docs.py`: feature / task / defect docs (LIKE over title, spec or body, symptom; closed items rank last). `KIND_ORDER` becomes feature, task, defect, session, pattern, file, span, and the global search fans out to the new kinds with caps of 3. New `lib/palette/resolve.py` + `GET /api/palette/resolve` implement R7: `F146` / `f:146` / `T…` / `D…` exact, bare number one row per kind plus text matches labelled `text`, miss note `No task T999` with no neighbour ids, `#`+<4 hex rejected. Tests go in `tests/fulltext/test_entity_docs.py` and `tests/palette/test_resolve.py

## Spec 依据

R1, R3, R7

## 验收测试名

- `test_feature_task_defect_are_searchable`
- `test_closed_entities_rank_last`
- `test_bare_number_resolves_every_kind`
- `test_miss_lists_text_not_neighbours`
- `test_short_hash_is_not_searched`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
