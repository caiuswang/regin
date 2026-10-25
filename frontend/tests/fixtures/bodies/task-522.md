## Delivers

Draw the seed and write `inspiration`; `storyboard.json`: the nine scenes of the Storyboard table with cards, bars and pins chosen by the criteria (swap the S1 track only under D4); `scenes.mjs` for the seven non-card scenes, each arriving by a click chain from the one before; tune bars until the runtime lands in 165–195 s; build `out/regin-promo-pixel.mp4

## Spec 依据

Storyboard, D3, D7–D10, R1–R3, R7, R9–R11, R13, R16

## 验收测试名

- `check_runtime_and_streams`
- `check_route_coverage_and_share`
- `check_cards_on_downbeat`
- `check_seed_hidden`
- `check_card_copy`
- `check_hero_frames_legible`
- `check_clean_frames`
- `check_rail_collapsed`
- `check_no_flash_or_hard_cut`

## 收尾动作

合回 `f162/integration`（这是报告完成的前置条件，不是可选步骤）
