## Delivers

Split `--pl-ink` into the role tokens of *Pixel dark palette* (text / frame / drop / bevel / paper tiers / sunk / tints / cells), with light values unchanged; set every dark value from the table; move sprite keys to `--pl-px-<key>` per the sprite table; re-point every pixel rule, slot and sprite at roles (no hex outside the palette blocks); fix the triage secondary-key contrast; e2e `pipeline-theme-dark.spec.js

## Spec 依据

R7, R15, "Pixel dark palette", I3

## 验收测试名

- `test_dark_palette_matches_the_design_by_role`
- `test_splitting_the_roles_leaves_light_untouched`
- `test_no_hard_coded_colours_in_the_pixel_theme`
- `test_dark_mode`

## 收尾动作

合回 `f139/integration`（这是报告完成的前置条件，不是可选步骤）
