## Delivers

lib/hooks_wiring.py` 的 json 安装路径接上 codebuddy-family：写进 `<root>/settings.json` 的 `hooks` 键，每个 `hook_events()` 事件一条 `python -P -m hook_manager <Event> --agent-type <provider>`，合并写入——用户原有 hooks 条目与其他顶层键逐字节保留；`cli/commands/hooks.py` 的 `install` / `status` / `repair` 认识两个新 provider，status 报告无漂移；`capabilities.hooks == false` 的 provider 一律不写。`lib/skills/skill_deployer.py` / `skill_sync.py` 把 pattern 部署为 `~/.codebuddy/skills/<id>/SKILL.md` 与 `~/.workbuddy/skills/<id>/SKILL.md`，frontmatter 含 name 与 description。测试全部在 tmp HOME 下跑：`tests/hooks/test_codebuddy_hooks_install.py`（Scenario Outline 两个 Examples 各一条参数化用例）、`tests/skills/test_codebuddy_skill_deploy.py

## Spec 依据

Rules 接入面矩阵（hooks、skills 行）, Invariants

## 验收测试名

- `test_codebuddy_family_hooks_install_is_claude_shaped_and_merge_safe`
- `test_skills_deploy_to_codebuddy_family_dirs`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
