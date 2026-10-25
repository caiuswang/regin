## Delivers

PoC `regin-band` 从 `~/.claude/dev-mods/ae818f96-650d-4339-b2b0-05c51597ebe0/regin-band/` 搬进 `regin-plugin/plugins/regin-band/`（含 `.claude-plugin/plugin.json`、`hooks/`、`types/`、`register.test.tsx`），场景标题改成 Acceptance 里的英文名；`marketplace.json` 登记 regin-band；`tests/mods/test_marketplace.py` 断言 I1；`regin-plugin/README.md` 写明 mod 的安装方式

## Spec 依据

分发, PoC, I1

## 验收测试名

- `test_marketplace_lists_every_plugin_dir`
- `test_unread_count_shows_above_the_prompt`
- `test_band_is_empty_when_inbox_is_clear`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
