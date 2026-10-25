## Delivers

插件 `register.tsx` / `register.ts` / `regin-plugin/README.md` 改用 `$REGIN_HOME`（默认 `~/regin`）。删除 `hook_manager/settings.example.json`、`hook_manager/migration_preview.py` 和 `scripts/create_distribution_zip.sh`；`hook_manager/README.md` 的接线一节改为指向 `regin hooks install`。`config/settings.json` 精简为中性默认，作者的值移到主 checkout 不入库的 `config/settings.local.json`。`lib/auth.py` 的 secret 路径改从 settings 的 config 目录解析。`lib/doctor.py` 展开 `~`，改走 provider 和 settings 的路径。前端没构建时 404 页提示执行 `./scripts/setup.sh`；启动时库落后 head 就打 warning，提示 `regin migrate`。`/api/hooks` 安装路由的注释去掉「legacy / debug only」

## Spec 依据

R7 插件路径, R7 样例文件, R7 config/settings.json, R7 auth, R7 doctor, R7 serve, 不变量 6

## 验收测试名

- `test_doctor_expands_home_in_bundle_paths`
- `test_jwt_secret_follows_the_config_dir`
- `test_shipped_plugins_carry_no_author_path`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
