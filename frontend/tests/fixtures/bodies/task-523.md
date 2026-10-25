## Delivers

Check that S1 and S2 merged back into `f162/integration` and neither landed zero commits. On the integration branch run `cd frontend && node promo/build.mjs && node promo/check.mjs`. Confirm I5 and I6 with `git diff --name-only master...HEAD`. Report the mp4 path, its sha256, its duration, the seed's presence in the manifest and absence from the film, and the per-check PASS lines

## Spec 依据

Work slices, Acceptance, Gates, I5, I6

## 验收测试名

- `test_every_slice_merged_back`
- `check_runtime_and_streams`
- `check_seed_hidden`
- `check_snapshot_isolation`

## 收尾动作

合回 `f162/integration`（这是报告完成的前置条件，不是可选步骤）
