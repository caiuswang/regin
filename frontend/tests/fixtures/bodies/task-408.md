## Delivers

lib/features/`：`GET /api/features/:id/branches` 按 C-R1–C-R4 实时读 git（integration 的 set/convention、runs 由 `params_json.worktree.branch` 派生、`git worktree list --porcelain`、`merge-base --is-ancestor`）；C-R7 repo 缺失时 200 + `error`；`PATCH /api/features/:id {branch}` 用 `git check-ref-format --branch` 校验；arm 预填所需的 integration name 也由该端点给出；测试放在 `tests/features/test_feature_branches.py`（用临时 git 仓库）

## Spec 依据

C-R1, C-R2, C-R3, C-R4, C-R6, C-R7, Non-goals

## 验收测试名

- `test_convention_branch_is_shown`
- `test_run_branches_are_derived_from_run_params`
- `test_bad_repo_does_not_500`
- `test_set_branch_validates_ref_format`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
