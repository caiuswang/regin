## Delivers

新模块 `lib/agent_bridge/mod_hold.py`：按 `(session_id, tool_use_id)` 登记挂起请求，状态机 held → resolved / released / gone，离开 held 即注销，同一时刻只有一种结局（J4）；断流用 select 等 EOF，做法同 `mod_channel.py`（J2）。`GET /api/bridge/hold`（bridge token，NDJSON）。CLI：`regin bridge hold`（stdin 读 HoldRequest，stdout 打一行 HoldOutcome；exit 0 得到结果，exit 3 被 release，连不上或断流用其它非零码）和 `regin bridge release`。`bridge-answer` / `bridge-decide` 按“网页上的应答走哪条路”表路由：命中就写进挂起流并审计 `channel=mod`；没带 `tool_use_id` 又有多个挂起时返回 409；没命中时 tmux 路径逐字节不变（J3）。ask 载荷按规则转换，permission 复用 `_parse_decision`。`live-state` 加 `mod_held`。待答卡片按 S1 定下的分支生成，同一个 `tool_use_id` 只有一张卡。测试放在 `tests/agent_bridge/` 和 `tests/web/

## Spec 依据

Model, 挂起请求的生命周期, 网页上的应答走哪条路, 卡片从哪来, J2, J3, J4

## 验收测试名

- `test_held_ask_is_answered_from_the_web_over_the_mod_channel`
- `test_multi_question_held_ask_maps_every_answer_by_question_text`
- `test_held_stream_ends_when_the_hold_process_dies`
- `test_answers_without_a_held_request_keep_the_tmux_path`
- `test_an_answer_only_reaches_its_own_held_request`
- `test_two_held_asks_without_a_tool_use_id_are_refused`
- `test_a_held_request_resolves_at_most_once`
- `test_live_state_lists_held_requests`
- `test_a_held_request_has_exactly_one_pending_card`

## 收尾动作

合回 `f158/integration`（这是报告完成的前置条件，不是可选步骤）
