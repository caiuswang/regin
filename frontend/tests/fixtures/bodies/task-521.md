## Delivers

Remove the narration path (`lib/tts.mjs`, the band, `captions.srt`, `storyboard.zh.json`, the three narration checks); `seed.mjs`; `ctx.beat` with bars, the downbeat wait and card scheduling on the grid; the overlay restyled from F154 tokens with Silkscreen served by the recorder, the logo/robot/bug sprites and `P_STAGES` imported from `website/src/content/play.js`, the pixel pointer; manifest fields `hero_settled`, `pl_theme_at_hero`, `theme_at_hero`, `caption_layers`, `cards`; `regin_pipeline_theme=pixel` seeded; mux with the music bed (fade-in ≤ 2 s, fade-out 2–4 s, two-pass loudnorm), output `regin-promo-pixel.mp4`; one track chosen under D4 committed with `music/LICENSE.md`; `check.mjs` with all fifteen checks; `storyboard.smoke.json` rewritten to the new shape

## Spec 依据

D1–D6, D8, D11, R4–R10, R12–R17, I1–I4, Shape

## 验收测试名

- `check_no_captions`
- `check_music_license_and_fit`
- `check_audio_mastered`
- `check_cards_on_downbeat`
- `check_pixel_theme`
- `check_seed_hidden`
- `check_card_copy`
- `check_pins_refuse_missing_id`
- `check_hero_frames_legible`
- `check_clean_frames`
- `check_snapshot_isolation`
- `check_rail_collapsed`
- `check_no_flash_or_hard_cut`

## 收尾动作

合回 `f162/integration`（这是报告完成的前置条件，不是可选步骤）
