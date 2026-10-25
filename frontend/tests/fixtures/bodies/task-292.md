## Delivers

frontend/promo/`: provisioner (online-backup snapshot, four redirected stores, `promo-admin`, `serve --replica` on :8323 + vite on :5175, teardown on exit including failure); `say` TTS per line with ffprobe durations; recorder (dsf 1.25 dark context, cursor + subtitle overlays, `ctx.line`/`hero`/`point`, non-GET route guard, console capture, pin resolution and refusal, manifest); mux (placed clips, loudnorm, H.264/AAC/mov_text, SRT); `check.mjs` with all nine checks; `storyboard.smoke.json` (intro card + overview); `.gitignore` entry for `out/

## Spec 依据

D1, D2, D4–D9, R5–R12, I1–I4, I6, Shape

## 验收测试名

- `check_subtitles_match_audio`
- `check_audio_loudness`
- `check_pins_refuse_missing_id`
- `check_hero_frames_legible`
- `check_clean_frames`
- `check_snapshot_isolation`

## 收尾动作

合回 `f127/integration`（这是报告完成的前置条件，不是可选步骤）
