## Delivers

regin-plugin/plugins/regin-ask/`：`tool.call{tool:AskUserQuestion}` 钩子从可注入的答案源取答案，`next({...e, input})` 改写；其它工具直接 `next(e)`；`*.test.ts` 覆盖两个场景；登记进 marketplace.json。查 `claude-code-tools` 类型里 AskUserQuestion 的 input 是否有答案字段，把结论（kit 内已验证 / 实时会话未验证，各附证据）写回本 spec 映射表第 3 行的「已验证」列

## Spec 依据

映射表第 3 行

## 验收测试名

- `test_ask_rewrite_reaches_the_tool_with_answers`
- `test_other_tools_pass_untouched`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
