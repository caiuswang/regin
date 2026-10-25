## Delivers

确认 S1–S4 都已合回 `f158/integration`，没有零 commit 的片，`marketplace.json` 的合并冲突已解决。在 DB 副本上跑 `.venv/bin/python cli/regin.py migrate` 和 `doctor`。全量跑 `.venv/bin/python -m pytest -q -n auto --dist=loadfile`。对 `regin-plugin/plugins/` 下的每个 mod 跑 `claude plugin validate` 和 `claude plugin test`。跑 Playwright：`live-approve-permission.spec.js`、`live-mod-held-permission.spec.js`、`trace-queued-send-now.spec.js`。在基线上也能复现的失败只报告，不修。通过 send_to_user 汇报输出

## Spec 依据

Acceptance, J1–J8

## 验收测试名

- `test_every_slice_merged_back`
- `test_migrate_leaves_no_schema_finding`
- `test_full_suite_passes`

## 收尾动作

合回 `f158/integration`（这是报告完成的前置条件，不是可选步骤）
