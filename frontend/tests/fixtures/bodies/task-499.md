## Delivers

lib/skills/skill_deployer.py` 部署和撤销前先查 `pattern_deployments.deployed_path`：目标目录存在但没有记录就拒绝并报出路径，不 `rmtree`。内置 skill（`grit-rules`、`python-complexity`）也写入同一份记录。测试放在 `tests/skills/

## Spec 依据

R6, 不变量 5

## 验收测试名

- `test_skill_deploy_refuses_an_unrecorded_directory`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
