## Delivers

regin 侧：`POST /api/sessions/<id>/bridge-send-now`：有 Mod 监听者时向它投递控制帧 `{"op":"abort"}`，审计 `channel=mod`；没有监听者时返回 409，不碰 tmux（I5）；`live-state` 增加 `mod_listener` 字段。Mod 侧：`regin-bridge` 用 `$.state` 记录运行中的 turnId（turn.start 写入，turn.complete 清空），收到 abort 帧调 `$.turn.abort({turnId})`，不提交 prompt，CC 自己会接着执行队列里的 prompt。前端：`TraceQueuedPrompts.vue` 在 `mod_listener` 为 true 时显示“立即发送”（打断当前 turn，让排队的 prompt 马上执行），新增 `frontend/tests/trace-queued-send-now.spec.js

## Spec 依据

第二轮 #1, I2, I4, I5

## 验收测试名

- `test_send_now_aborts_the_running_turn`
- `test_abort_frame_without_a_running_turn_is_ignored`
- `test_send_now_goes_over_the_mod_channel`
- `test_send_now_refuses_without_a_mod_listener`
- `test_live_state_reports_the_mod_listener`
- `queued prompts offer send now only with a mod listener`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
