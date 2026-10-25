## Delivers

regin-ask 改为 hold：`e.answers` 已有值就直接 `next(e)`；否则显示挂起横条、调 `$.ui.notify`，然后 `await $.process.run` 调 `regin bridge hold`，按“hold 结果 → mod 怎么返回”表返回。删掉 userConfig `answerUrl`（J1）。横条上的“在这里回答”按钮调 `regin bridge release`；如果 S1 的 Q5 不成立，就去掉按钮，按 spec 把 J5 降级。新 mod `regin-permit`：在 `tool.check` 里先 `r = await next(e)`，只有 `r.decision === 'ask'` 且有 `tool_use_id` 时才挂起。两个 mod 都用 CC 2.1.295 重新生成 `types/`，带 `*.test.ts`，并登记进 `marketplace.json`（J8）。前端：`LiveQaDecision.vue` 在 `live-state.mod_held` 含该卡片的 `tool_use_id` 时只显示 Allow / Deny，点击后 POST `bridge-decide {tool_use_id, behavior}`；新增 Playwright spec `frontend/tests/live-mod-held-permission.spec.js

## Spec 依据

mod 侧钩子, hold 结果 → mod 怎么返回, J1, J5, J8

## 验收测试名

- `test_ask_hook_rewrites_the_tool_input_with_held_answers`
- `test_chat_over_a_held_ask_denies_with_the_operator_s_text`
- `test_released_ask_falls_back_to_the_native_dialog`
- `test_unreachable_regin_falls_back_to_the_native_dialog`
- `test_permission_ask_is_held_and_allowed_from_the_web`
- `test_permission_deny_carries_the_reason_to_the_model`
- `test_settled_permissions_are_not_held`
- `test_held_permission_card_offers_allow_and_deny`
- `test_marketplace_lists_every_plugin_dir`

## 收尾动作

合回 `f158/integration`（这是报告完成的前置条件，不是可选步骤）
