## Delivers

K1–K6: `lib/defects/suites/` pytest + playwright adapters; `settings.defects.check_suites`; `store.close` confirms or refuses, `--no-check`; columns `check_suite`/`check_id` (alembic + schema.sql + model); ledger and `FeatureFigures` count confirmed; web close form shows the refusal and the no-test toggle; `lib/defects/checks.py`, `tests/defects/test_missing_check_reading.py` and the three old `GAP_BLURB` keys deleted

## Spec 依据

D7, R7, I5, I8

## 验收测试名

- `test_close_confirms_check_from_collector`
- `test_close_refuses_uncollected_check_with_nearest`
- `test_close_no_check_requires_reason`
- `test_no_suites_configured_draws_no_check_figure`
- `test_ledger_counts_confirmed_checks_only`
- `test_defect_close_form_refusal_and_no_check`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
