## Delivers

registry.js`, `usePipelineTheme`, `PlSlot`/`PlGlyph`, `data-pl-part` hooks on shell + tab components, `html[data-pl-theme]` lifecycle, header switch, classic module = existing components, pixel module stubbed empty; e2e `pipeline-theme-seam.spec.js

## Spec 依据

R1, R2, R3, R5, R6, I1, I2, I4

## 验收测试名

- `test_default_is_classic`
- `test_classic_is_unchanged`
- `test_switching_keeps_place`
- `test_unknown_stored_value`
- `test_theme_attribute_is_scoped_to_the_shell`
- `test_a_theme_chunk_that_fails_to_load`
- `test_behaviour_is_theme_independent`

## 收尾动作

合回 `f139/integration`（这是报告完成的前置条件，不是可选步骤）
