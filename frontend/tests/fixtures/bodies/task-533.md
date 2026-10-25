## Delivers

alembic/versions/00NN_workspaces.py` + `db/schema.sql` + `lib/orm/models/`（`Workspace`）。新增 `lib/workspaces/`：create（扫描根目录本身和直接子目录，未注册的调 `lib/sync/repo_discovery.add_repo`）、list（成员按路径推导）、remove。`web/blueprints/` 新增 `GET/POST /api/workspaces`、`DELETE /api/workspaces/<id>`（editor），`web/blueprints/repos.py` 新增 `GET /api/repos/probe`。CLI `regin workspace add/list/remove`。测试 `tests/workspaces/

## Spec 依据

Model, Rules C, AddProjectDialog 分支表, Invariants

## 验收测试名

- `test_creating_a_workspace_registers_its_child_repos`
- `test_creating_a_workspace_with_no_repos_is_refused`
- `test_repo_registered_later_under_the_root_becomes_a_member`
- `test_removing_a_workspace_keeps_its_repos`
- `test_cli_workspace_commands_round_trip`
- `test_probe_classifies_a_folder_of_repos_as_a_workspace`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
