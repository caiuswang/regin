## Delivers

Check that S1–S4 are each merged into `f125/integration` with at least one commit. On the integration branch, run the full pytest with the baseline red deselected, and the F131 Playwright specs plus the twelve regression specs in Verify. A failure that reproduces on the Gates baseline is reported, not fixed. Then, in `/Users/taowang/regin` on `f125/integration`: `cd frontend && npx vite build`. If no open run was launched through the SDK, stop the process listening on :8321, relaunch `.venv/bin/regin serve` detached (`nohup … >> /tmp/regin-serve.log 2>&1 &`) and wait for GET /api/auth/me to answer 200. Live-check on :8321 without creating data: New feature → "@" lists Recent and the six kinds; /features/131 renders this spec's references as chips; /features/125 shows Referenced by F131; a prompt rendered after the restart for S5's own task (run_prompt._prompt) holds a references block listing F125. Report each check with its evidence

## Spec 依据

Work slices, Acceptance, Gates, I1–I12

## 验收测试名

- `test_every_slice_merged_back`
- `test_scoped_suites_pass_on_integration`
- `test_live_check_on_8321`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
