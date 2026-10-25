## Delivers

The only full run for the whole feature: `.venv/bin/python -m pytest -q -n auto --dist=loadfile`, `cd frontend && npx vite build && ./node_modules/.bin/playwright test`. Fix failures in place. Then **product check**: start the integrated app (`regin serve` on a spare port + vite), open ⌘K and ⌘J in a real browser against real data, and screenshot each state in Screens 1–10 into `/tmp/f146-product/NN-<state>.png`. Compare every image with its design image, fix any difference in place, and post the comparison via `send_to_user

## Spec 依据

Acceptance

## 验收测试名

- `test_full_suite_passes`
- `test_product_matches_screens`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
