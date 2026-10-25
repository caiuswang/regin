## Delivers

_references_block` in lib/tasks/run_prompt.py (A2–A5), reading the full body and calling resolve_refs from S1 with can_read_sessions true; a new surface task-run-references in lib/prompts/surfaces/tasks.py; `{{references}}` added to `_LAUNCH_BODY` before `{{finish}}`, declared as a PromptVariable, with the previous default's sha256 registered with register_retired_default (A1, D9)

## Spec 依据

D5, D9, R7

## 验收测试名

- `test_run_prompt_lists_references_with_read_commands`
- `test_run_prompt_without_references_has_no_block`
- `test_references_past_the_body_cut_are_still_listed`
- `test_unedited_launch_envelope_upgrades_to_carry_references`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
