## Delivers

regin-plugin/plugins/regin-attribution/`：`attribution.text` 钩子，kind 为 commit 或 pr 时先 `await next(e)`，再删掉 `Claude-Session:` 行和裸的 `https://claude.ai/code/session_…` 链接，其余文字逐字保留（I6）；其它 kind 直接 `next(e)`；`*.test.ts` 覆盖三个场景；登记进 marketplace.json（I1）

## Spec 依据

第二轮 #2, I1, I6

## 验收测试名

- `test_commit_attribution_drops_the_session_trailer`
- `test_pr_attribution_drops_bare_session_links`
- `test_other_attribution_text_passes_untouched`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
