## Delivers

钩子里的挂载腿：`attributions_for_session` → 解析 → 挂载段排在最前 → `memory.recall.mount` span；`mount_max_entries` / `mount_max_chars` / `mounts_enabled

## Spec 依据

R3, R4, Invariant 2, Invariant 5

## 验收测试名

- `tests/hooks/test_mount_injection.py`
- `tests/hooks/test_mount_attribution.py`

## 收尾动作

合回 `master`（这是报告完成的前置条件，不是可选步骤）
