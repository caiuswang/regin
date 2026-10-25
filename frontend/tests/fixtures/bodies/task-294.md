## Delivers

Check that S1–S3 are all merged back into `f127/integration` and none landed zero commits. On the integration branch, run `.venv/bin/python -m pytest -q -n auto --dist=loadfile` and `cd frontend && node promo/build.mjs && node promo/check.mjs`. Report the mp4 path, its sha256, its duration and the per-check PASS lines. Confirm I5 with `git diff --name-only`.

## Spec 依据

Work slices, Acceptance, Gates, I5

## 验收测试名

- `test_every_slice_merged_back`
- `test_full_suite_passes`
- `check_runtime_and_streams`
- `check_snapshot_isolation`

## 收尾动作

合回 `f127/integration`（这是报告完成的前置条件，不是可选步骤）
