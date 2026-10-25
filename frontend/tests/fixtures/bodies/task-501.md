## Delivers

按 R1 表重写 `scripts/setup.sh`：Python 低于 3.11 或 Node/npm 不满足就退出并说明原因；每次都执行 `pip install -e` 和 `npm ci && vite build`；DB 改为 `regin init`（不带 `--force`）加 `regin migrate`；不调用 `rebuild`；询问是否 `regin hooks install --provider claude`（默认 Y），已接线则 `regin hooks repair`；已有用户就不再创建；standalone 分支不再调用 `users init-db`。末尾打印升级和卸载命令。`pyproject.toml` 改为 `requires-python >=3.11`，`mcp` 加入基础依赖。测试用临时 checkout 非交互地跑 setup.sh

## Spec 依据

R1, R7 pyproject, R7 users init-db, 不变量 1

## 验收测试名

- `test_rerunning_setup_keeps_the_database`
- `test_setup_refuses_python_older_than_3_11`
- `test_base_install_provides_mcp`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
