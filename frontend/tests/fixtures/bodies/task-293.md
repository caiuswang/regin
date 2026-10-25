## Delivers

storyboard.zh.json`: all 11 scenes, final Chinese narration (with `speak` overrides where `say` mispronounces a term), pins chosen from the snapshot (feature #125 unless it lacks an ended session); `scenes.mjs` actions for all nine non-card scenes, tuned until the runtime lands in 180–300 s; `out/regin-promo-zh.mp4` built

## Spec 依据

Storyboard, D4, D5, D10, R1–R4, R9, R11

## 验收测试名

- `check_runtime_and_streams`
- `check_route_coverage_and_share`
- `check_narration_is_chinese`
- `check_clean_frames`
- `check_hero_frames_legible`

## 收尾动作

合回 `f127/integration`（这是报告完成的前置条件，不是可选步骤）
