# F162 — regin promo video: music-only, pixel-game cut in Chinese and in English (~3½ min)

## Intent

A promotional video of regin, about three minutes long, aimed at a Chinese audience, that answers three
questions before it ends: what regin is, which coding agents it drives, and what it is for. It opens on a
hook — a cover asking **「现代 AI 工作台，应该长什么样？」** — answers it with an outline of six levels, and then
plays those six levels on one real feature, from spec to ship, recorded from the real SPA. Each level shows
one part of the product only, and opens on its own screen: RUN is the task and its run, WATCH is the session (its facts,
Conversation view and Presentation Mode), COUNT is the feature's own defects and the fix task's thread, SHIP the ledger. It carries **no
narration and no subtitles**: a licensed instrumental track, the picture, and Chinese level cards do the work.
Its chrome (cards, pointer, transitions) is the website's **game-pixel style** (F154 tokens, F139 pixel theme on
the dashboard's shell tabs): between levels a pixel-block wipe and a robot hopping along a level map. A random
six-digit **seed** is drawn once and used as the creative starting point for the cut; the seed itself is
never shown or derivable from the film.

The same film is also cut in **English for X (Twitter)**: its cards in English, and the app's own data — the
operator's features, tasks and defects, written partly in Chinese — read through a glossary on the way to the
screen, so no Chinese reaches the frame. It is delivered twice: the 4K master, and a 1080p file cut to X's
upload rules.

The briefs, verbatim:
- 参考之前的视频里面的模块，不要朗读了，也不用加字幕，自己找个合适的音乐，要符合宣传，并且美观，按照模块，全流程的那种，类似与我们
  Website里的那种游戏像素风格。使用一个随机号码种子，用这个号码来做灵感启动，但是宣传视频里不要有这个种子的信息，这个只用来做灵感。时长大概在3分钟左右。
- 目前这个宣传视频前面暂停的时间太长，而且没有说完一个完整的流程，包括前几秒没有抓住用户，……而且有些关卡停留只有英文，但是目前我们受众主要
  是中文。而且用户看完也不知道你这个能干啥，支持什么Agents, 能用来干啥。前面也需要有一个大纲展示，包括封面图。不同关卡之间跳动可用模拟游戏风格那种。
  The operator added: the Trace detail may show the Conversation view only; the film may run longer than 3 min; the Pipeline
  tabs are scoped to the regin project so no other project shows; the worked example is the game-pixel-style feature (#139);
  the hook is "what should a modern AI workbench look like"; levels must be clearly distinct — the session Conversation view,
  Presentation Mode and the reply chat box belong to a WATCH level.
- 我准备把这个再发在 Twitter 上一条，之前中文版整体差不多，但是看一下 Twitter 有什么限制没有？还有就是把里面的中文都替换成英文。还有目前看那个
  Feature 详情的时候，我看你点了一张图片，但是那张图片里放大了根本看不清细节，找一下其他的图片。

The video is produced by the checked-in, re-runnable harness in `frontend/promo/` (storyboard → Playwright
screencast of a DB snapshot served read-only → ffmpeg). When the UI changes, the video is regenerated.

Non-goals:
- the `frontend/film/` WebGL engine (《天亮以前》, F128). Retired for this line of work: nothing in it is run, reused or touched;
- any voice, caption or on-screen sentence synchronised to the footage — no burned-in band, no soft subtitle track, no SRT/VTT. Words live on cards only;
- product changes. No page is changed to look better on camera. A page that renders wrong is a defect against its own feature;
- creating a feature on camera: the replica refuses writes (D11), so the film walks a feature that already shipped;
- the session's follow-up chat box: it renders only for a session regin can steer right now (`bridge_reachable`), and the replica runs
  with the agent bridge off — turning it on would let the replica's terminal socket type into live tmux panes. Replying to an agent is
  shown where the replica can show it truthfully: the fix task's comment thread, where the agent's report sits and a reply (typed, never
  sent) reaches the task's next attempt. The pinned run's TURN 2 is regin's automatic resume after a usage limit, not a person, and is
  not shown as a reply;
- publishing or uploading; a vertical cut; localising the UI; a synthetic dataset — the English cut translates the replica's real
  rows on their way to the screen, it never invents or edits one;
- a persistent HUD frame around the footage, a Guidance chapter (`/memory`, `/patterns`, `/rules`) — see Follow-ups.

## Decisions (operator approves with the spec)

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | **Harness = F127's `frontend/promo/`**: Playwright CDP screencast of the real SPA, served from an online-backup snapshot by `regin serve --replica` on :8323 + vite on :5175, muxed by ffmpeg. 1280×720 CSS × device scale 3 → **3840×2160, 30 fps CFR**, H.264 high@5.1 CRF 16; one SPA document; every change of view inside a level a visible click dissolved beneath a freeze plate (560 ms); eased glides; camera zoom. | (a) `frontend/film/`. (b) Remotion. (c) A manual screen recording. | (a) Retired by the operator. (b) Composes a picture of the UI instead of recording it. (c) Not reproducible. |
| D2 | **The music cut supersedes the narrated cut.** No `lib/tts.mjs`, no subtitle band, no `captions.srt`, no `storyboard.zh.json`. The narrated cut stays reproducible from git history (`7c6002dd`, `99774b34`, `c0352c38`). | A dual-mode harness | Two modes double every overlay, mux and check path. |
| D3 | **Bars, not seconds.** The storyboard declares `bpm` and the first downbeat `downbeat_s`; the film's t0 is the music's t0. Every beat declares `bars`; a beat ends when its action and its bars are done. Every card after the opening one starts on a **downbeat**; inside a card every entrance, the robot's hops and the wipe are pinned to beats from that downbeat; looping sprites (walk, hop, blink) run whole beats per cycle and restart on downbeat 0 once t0 is known. The recorder's only clock waits are "until the next downbeat" and "until this beat's bars elapse". | Hand-tuned millisecond holds | Drifts on every edit and ignores the music. |
| D4 | **Music: one licensed instrumental track**, 100–140 BPM, at least the film's length + 1 s (never stretched or looped), CC0 1.0 or CC BY 3.0/4.0, committed as published under `frontend/promo/music/` beside `LICENSE.md`. The outro card carries the credit. Chosen: *Jump2Sky* — Oblidivm (OpenGameArt, CC BY 4.0, 140 bpm, first downbeat 1.325 s, 306 s). | Platform libraries; NC/ND/SA licences; a generated chiptune | Redistributing the file, commercial use, the "美观" bar. |
| D5 | **Pixel chrome from the website, two pixel faces.** Tokens from `website/src/assets/site.css`; sprites (`ROBOT_FRAME_1/2`, `BUG_ROWS`, palettes) and the stage kickers (`P_STAGES[i].kicker`) imported from `website/src/content/play.js`. Latin HUD text and the wordmark in Silkscreen (the pixel theme's own woff2); **Chinese in Fusion Pixel 12px proportional zh_hans** (OFL-1.1, committed unmodified with its licences under `frontend/promo/fonts/`), set only at multiples of 12 px. Both are served to the page by the recorder's route handler. Any face drawing a numeral is Silkscreen 400 (700 fuses digits — defect #263). The English cut sets its card headlines in Silkscreen 400 at multiples of 8 px and its lines in Fusion Pixel's Latin. Pointer a stepped pixel arrow; click ripple a square pulse. | (a) Silkscreen only, Latin cards. (b) A system CJK font. (c) Copying sprites into promo/. | (a) The audience is Chinese (operator). (b) Breaks the pixel look. (c) Drifts from the website. |
| D6 | **Footage theme and scope:** the recorder seeds `regin_theme=dark`, `regin_pipeline_theme=pixel`, `regin_sidebar_collapsed=1`, and `regin.pipeline.project=<id>` where `<id>` is the snapshot's repo named `storyboard.project` (`regin`), resolved at build time; an unresolved project refuses the build like a pin. A link that arrives with a feature filter (the ledger's Graph link) leaves the stored scope unread, so the scene names the project in the address beneath the plate — what a fresh load of it does — and never opens the project picker, whose list names every other project. | Opening the picker on camera | It shows every other project's name. |
| D7 | **Cards are game screens in the cut's language** (Chinese below; the English cut's words — `WORLD 1-n`, `START`, `▶ GO`, `YOU WIN!` — in `storyboard.en.json`). Four kinds: `cover` (frame 0: the hook question, wordmark, 支持 Claude Code · Kimi Code, a hopping robot, PRESS START, over the dimmed overview), `outline` (the answer: six level rows, a name and one line each, entering one per 1.5 beats), `level` (one per level: a map strip of 起点 + six nodes; the robot hops from the last node to this one in three beats and lands on the fourth; cleared nodes ★; then `第 n 关`, the level name, two objective lines and a blinking ▶ START), `outro` (通关！, wordmark, tagline, four abilities, the agents, `regin serve`, the site, the music credit). A level card's HUD is `LEVEL n · <word>`, numbered as the film plays the levels; the word is the website's own — the `P_STAGES` kicker's (SPEC/SLICE/RUN/COUNT/SHIP) and, for WATCH, the Observability layer's `verb` in website `features.js`. (`STAGE n` numbers would disagree with the level numbers once WATCH sits between RUN and COUNT.) Level names, lines and stages live once in `storyboard.levels` and feed both the outline and the level cards. | (a) English stage cards with `P_STAGES` titles. (b) An RPG-dialogue hook. (c) Five levels with the trace inside RUN. | (a) The audience is Chinese. (b) Rejected by the operator. (c) The operator: the levels did not read as distinct; the conversation is WATCH. |
| D8 | **Seed.** `node promo/seed.mjs` prints `crypto.randomInt(100000, 1000000)`. The storyboard carries `seed` and `inspiration` (3–6 lines on what the seed suggested). The digits reach no frame, no file name, no container tag. | A deterministic mapping table | The seed is a creative starter, not configuration. |
| D9 | **Cards state no figure read from the data**: the words on cards are the storyboard's plus the wordmark. Figures appear only in the footage. | Quoting the ledger on a card | The snapshot is re-taken on every build. |
| D10 | **Pins**: one shipped feature, one of its slice tasks with a passed verify, one of its closed defects with a missing check and a fix task, and that task's run session (`ended`); the storyboard names the ids; an unresolved pin refuses the build before recording. Chosen: feature **#139** (重新设计Pipeline操作界面-游戏像素风格 — the pixel UI the film is shot in), task **#360** (S3 — Overview), defect **#263** (fixed by task #384), session `ff040c3d…` (run #343 of #360). | A feature created on camera | The replica refuses writes (D11). |
| D11 | **Isolation**: the snapshot, `promo-admin` in the snapshot only, the replica's 403 on writes, the browser's route guard, teardown on exit including failure. | — | — |
| D12 | **Transitions are game transitions.** Footage → card and card → footage: a pixel-block wipe (32×18 blocks in an 8×8 Bayer dither over a diagonal sweep, ranked so the same number of blocks changes every frame, 720 ms) drawn on one canvas. Card → card over laid blocks: the fields step out and in (`steps()` easing). Inside a level, views still change by a dissolved click (D1). A camera move the 4K renderer redraws late — out of a deep zoom over a modal's blurred backdrop, where it draws ~6 frames a second and once came back black — is a cut instead: the camera moves beneath a plate and the new framing dissolves in, linear, over 3 s (`ctx.cut`). | Opacity fades; one DOM element per block; the unranked dither | A fade does not read as a game; 576 animated DOM blocks drop the 4K renderer to ~8 fps and start the wipe ~0.4 s late; the unranked sum piles blocks up mid-wipe and one skipped capture frame there steps the luma past R17. |
| D13 | **RUN shows the run, WATCH shows the session**, and no screen appears in two levels. RUN: the task, its run history with *verify passed*, out through the run's session link. WATCH: the session's header facts (model, spans, tokens, cost), then its Conversation view only — the brief, opened in Presentation Mode (the span reader); the turn's tool chips with a failure; the verifier sub-agent's failing report; the agent's done-report. No Agents roster, Timeline, Messages view or sessions list. | A tour of every trace view; the trace header in both RUN and WATCH | Operator's calls; a verifier found the header, the ledger and the Acceptance block each framed in two or three levels. |
| D14 | **No dead air**: a freeze plate stands still only while the next view settles. Each `storyboard.warm` route (the pinned trace, the pinned feature's ledger) is loaded once in a second tab before t0, until its `ready` selector shows and that tab's reads land; the GET responses under its `replay` prefixes (`/api/sessions/{session0}/`, `/api/features/{feature}/branches`) are kept by exact URL and answer the camera's page from then on — they are slow (the trace's `/map` ~2.6 s every time, the ledger's git branches ~1.6 s) and deterministic on a read-only snapshot. Non-GET and unkept requests fall through to the write guard. API reads are tracked per page, never per context (a closed tab's in-flight read never reports finished). Every plate hold is recorded in the manifest (`holds`), and the replayed URLs actually served (`replayed`). A beat whose action leaves ≥ 2.2 s of its bars spends them leaning the camera in (×1.06 about the pointer) — the last beat before a card counts the wait for that card's downbeat among them, and stops a lead short of it; only from the scene's frame, never on top of a zoom, whose blocks it would push past the frame's edge; reading beats zoom slowly instead (2.6 s). The camera is reset beneath every card. | (a) A longer beat around a slow click. (b) Warming the server only. | (a) Defect #298: the run-history modal sat still for ~8 s. (b) Measured: still 4.4 s, the slow read is compute, not cache. |
| D15 | **COUNT walks the feature's own defects** on its ledger: the pinned defect's card (symptom, missing check, fix item); its fix task, where the agent's report sits in the task's comment thread and a reply is typed into it (from `scene.typed`, never sent); back to the feature. | The all-defects list | Its top rows are whatever is open now — at build #4, this film's own bug report #298 and an unrelated Kimi defect. |
| D16 | **Each level opens on its own content, and the frame holds only the film's subject.** A scene may name `open_at` (a section scrolled to the top beneath its level card) and `frame` (a standing camera: scale k, page top at the frame's top, page x at its left), re-applied beneath every dissolve plate. The overview's frame keeps the in-flight list (where this film's own feature sits) below the frame; the trace's keeps the session status rail (which lists live sessions, this run's among them) out — from the moment RUN's click lands on the trace, which takes WATCH's frame beneath its plate (`ctx.frameAs`). A beat-start check counts on-screen text naming the film's own feature (`storyboard.self`). | Scrolling on camera at each level's start | That opened each level on the last level's screen. |
| D17 | **Two cuts, one harness.** `storyboard.json` is the Chinese cut (`lang` absent = `zh`); `storyboard.en.json` (`lang: "en"`) is the same film — scenes, beats, bars, pins, seed — in English words. A scene function never names a language; where it selects by the app's data it takes the phrase through `ctx.word`. | (a) Subtitles over the Chinese cut. (b) A separate English edit. | (a) No captions (R4). (b) Two films drift; the operator asked for the same film. |
| D18 | **The English cut reads the app's data through a glossary.** `storyboard.translate` names `i18n/en.json`, a map of Chinese phrase → English. The recorder rewrites every GET `/api` response (every JSON string, longest phrase first) before the page sees it, so a phrase is translated wherever it recurs — titles, task briefs, span payloads, the replayed reads. The snapshot and the live DB are never touched. A phrase the glossary lacks stays as written: R29 fails naming it in frame, and `manifest.untranslated` lists those read but never shown. | (a) Editing the snapshot's rows. (b) Machine translation at build time. (c) English cards over Chinese footage. | (a) Mutates the replica the isolation story rests on (D11). (b) Unreviewed words on camera, a network call in the build. (c) The operator: 把里面的中文都替换成英文. |
| D19 | **An X-ready file beside the master.** A storyboard with `exports: ["x"]` also writes `regin-promo-x.mp4`: 1920×1080 30 fps CFR, H.264 High 4.2, yuv420p limited range (the screencast JPEGs are full range, so the range is converted, not relabelled), two-pass 8 Mb/s, closed 2 s GOP, the master's AAC-LC copied, faststart. From docs.x.com media best practices (read 2026-10-21): a post video runs up to 20 min / 8 GB (125 min / 16 GB for Premium), 140 s is the DM cap, ≥ 5 Mb/s video is recommended, non-Premium accounts play 720p. The help centre's older 2:20 / 512 MB could not be fetched (403); the file stays under 512 MB. | (a) Uploading the 4K master. (b) A 2:20 cut. (c) CRF only. | (a) 4K is downscaled anyway, at 1.4× the bytes. (b) The docs no longer cap posts at 140 s; an uploader trim would also cut the outro's CC BY credit. (c) Screen footage settles near 2 Mb/s at CRF 14, under X's 5 Mb/s. |

## Shape

```ts
// frontend/promo/storyboard.json — the ONLY place on-screen words live (I2)
interface Storyboard {
  title: string
  lang?: 'zh' | 'en'                 // the cut's language, zh when absent, D17
  translate?: string                 // glossary of the app's data, promo-relative; required unless zh, D18
  exports?: 'x'[]                    // extra deliverables, D19
  seed: number                       // 100000–999999, D8
  inspiration: string[]              // 3–6 lines, D8
  project: string                    // repo name the Pipeline tabs are scoped to, D6
  music: Music
  pins: { feature: number; task: number; defect: number; sessions: string[] }   // D10
  warm?: { route: string; ready: string; replay?: string[] }[]                    // D14
  levels: { stage: 'SPEC' | 'SLICE' | 'RUN' | 'WATCH' | 'COUNT' | 'SHIP'; name: string; line: string; body: [string, string] }[]   // D7, six
  self: string[]                     // text that names the film's own feature (its title), R28
  scenes: Scene[]
}
interface Music { file: string; sha256: string; bpm: number; downbeat_s: number
                  license: 'CC0-1.0' | 'CC-BY-3.0' | 'CC-BY-4.0'; credit: string }
interface Scene {
  id: string; surface: 'card' | 'pipeline' | 'session'
  route: string | null               // may use {feature} {task} {defect} {session0}
  hero: string | null
  card?: Card
  open_at?: string                   // D16: the section put at the top beneath the level card
  frame?: { k: number; x?: number }  // D16: the standing camera
  typed?: Record<string, string>     // words the camera types into the app (I2)
  beats: { id: string; bars: number }[]
}
type Card =
  | { kind: 'cover'; title: string /* "\n" breaks */; label: string; chips: string[]; hud: string }
  | { kind: 'outline'; title: string }
  | { kind: 'level'; level: number /* 1..levels.length */; hud: string /* the website's kicker for its stage */; no: string; start: string; go: string }
  | { kind: 'outro'; hud: string; title: string; tagline: string; tiles: string[]; label: string
      chips: string[]; caption: string; url: string; credit: true; credit_label: string }

// frontend/promo/out/manifest.json (gitignored), beyond F127's fields
interface Manifest {
  project: { name: string; id: number }
  lang: 'zh' | 'en'
  fonts: Record<string, boolean>                 // every face, loaded at boot
  han_beneath_cover?: string[]                   // R29, en: Chinese in frame beneath the opening card
  untranslated?: string[]                        // D18: Chinese left in any read, shown or not
  cover: 'cover.png' | null                      // out/cover.png: the opening card at 3840×2160
  holds: { scene: string; beat: string; what: string; start_s: number; held_s: number }[]   // D14
  replayed: string[]                             // D14: the kept reads, path + query
  scenes: { /* … */ hero_url: string; links_at_hero: { features: number[]; tasks: number[]; defects: number[] }
            beats: { /* … */ path: string; view: string | null; reader: boolean }[]          // view = trace view mode; reader = Presentation Mode up
            han_samples?: number; han_seen?: { at: string; beat: string | null; text: string[] }[] }[]   // R29, en
  cards: { id: string; kind: Card['kind']; opening: boolean; start_s: number; end_s: number
           text: string[]; fonts: { field: string; text: string; font: string }[]; wipe_in: number; wipe_out: number; hops: number }[]
}
```

Commands (from `frontend/`):
```
node promo/seed.mjs
node promo/build.mjs [--storyboard promo/storyboard.json] [--out promo/out/<dir>]
node promo/build.mjs --storyboard promo/storyboard.en.json --out promo/out/en      # the English cut + regin-promo-x.mp4
node promo/check.mjs [--out <dir>] [<check-name> …]
```

### Storyboard (what each part must land)

| # | id | card / surface | route(s) | Must land on screen |
|---|---|---|---|---|
| 0 | cover | cover (frame 0) | — (the overview loads beneath it, dimmed) | 「现代 AI 工作台，应该长什么样？」, wordmark, 支持 Claude Code · Kimi Code; also written to `out/cover.png` |
| 1 | outline | outline | — | 从需求到交付，一关一关打通 + 写规格 / 拆任务 / 跑 Agent / 看过程 / 记缺陷 / 交付, each with its line |
| 2 | overview | pipeline (frame 1.15) | `/overview?project=<regin>` | the loop scene, the regin project panel |
| 3 | spec-card → spec | LEVEL 1 · SPEC → pipeline, opens at #139's row | `/features` → `/features/139` | the project's features, #139's ledger, its game-dialog mockup (image 11: NEW LEVEL, NEW QUEST, SECRET STAGE) opened in the lightbox, pushed in ×2.2 over 5.5 s on a curve that grows its lit area evenly (each drawn step a small brightening, R17) and cut back out (D12), the Acceptance block (zoomed) |
| 4 | slice-card → slice | LEVEL 2 · SLICE → pipeline, opens at Slices | Slices → `/tasks?feature_id=139&view=graph` → List | the Slices table, the dependency graph (S1 → S2 → S3), the tasks by next step |
| 5 | run-card → run | LEVEL 3 · RUN → pipeline | `/tasks/360` | Delivers, acceptance test names, run history with *verify passed*; out by the run's session link |
| 6 | watch-card → watch | LEVEL 4 · WATCH → session (frame 1.06) | `/trace/sessions/ff040c3d…` | the header facts (zoomed); the Conversation view; the brief in Presentation Mode; the tool chips (a failure); the verifier's FAIL; the done-report; out by the header's feature link |
| 7 | count-card → count | LEVEL 5 · COUNT → pipeline, opens at Defects | `/features/139` → `/tasks/384` | #263's card: symptom, missing check (zoomed), fix item; the fix task, its agent's report in the comment thread, a reply typed |
| 8 | ship-card → ship | LEVEL 6 · SHIP → pipeline | `/features/139` | the ledger's stage line and cost / work-time figures (zoomed) |
| 9 | outro | outro | — | 通关！, regin, 给 AI 编程 Agent 的工作台, 规划功能 / 派发 Agent / 追踪会话 / 统计缺陷, 支持 Claude Code · Kimi Code, `$ regin serve`, regin.ccday.top, the music credit |

## Rules

| # | Rule | Checked by |
|---|---|---|
| R1 | One H.264 stream 3840×2160 30 fps CFR, one AAC stream, no subtitle stream; duration **150–300 s**. | check_runtime_and_streams |
| R2 | The scenes cover `/overview`, `/features`, `/features/:id`, `/tasks`, `/tasks/:id`, `/trace/sessions/:id` and visit no other route — in particular not `/trace/sessions` or the all-defects list. | check_route_coverage_and_share |
| R3 | Cards ≤ 28 % of runtime; pipeline scenes ≥ 40 %; session scenes ≥ 15 %. | check_route_coverage_and_share |
| R4 | No captions: no subtitle stream or file; no `lines`/`voice` on the storyboard or a scene; 0 caption layers at every beat; no `tts.mjs`, no `say`, no SRT writer. | check_no_captions |
| R5 | Music licence and fit as D4; the outro shows `music.credit`. | check_music_license_and_fit |
| R6 | −18…−14 LUFS, true peak ≤ −1.0 dBTP, no 0.5 s silence between 2 s and runtime − 4 s, faded head and tail. | check_audio_mastered |
| R7 | Every card but the opening one starts within 60 ms of a downbeat; every beat held its bars and overran by ≤ 1 bar. | check_cards_on_downbeat |
| R8 | Shell-route heroes are `pixel`; every hero is `dark`. | check_pixel_theme |
| R9 | The seed is hidden: storyboard and manifest only. | check_seed_hidden |
| R10 | Cards: the film opens on `cover` and ends on `outro`; one `outline`; one `level` card per `storyboard.levels` entry, numbered in order, whose HUD is `LEVEL n · <stage>` with the stage words the website's (the five `P_STAGES` words once each, in order, plus `WATCH`, whose `verb: 'WATCH'` `website/src/content/features.js` must still carry); every drawn field is a storyboard string or the wordmark; every card of a `zh` cut draws Chinese and no card of an `en` cut does; no field over 40 chars; HUD matches `/^[A-Z0-9 ·\-–▶]+$/`; holds — cover 2.5–4.5 s, outline 3.4–8 s, level 3.4–7 s, outro 5–12 s; the outline lists the levels in order; D9 by the verifier. | check_card_copy |
| R11 | Every click is preceded by a visible pointer move to its target; the hotspot lies inside the target at click time or the recorder refuses; every change of view inside a level is such a click, dissolved. | recorder-enforced; verifier |
| R12 | An unresolved pin or project refuses the build before recording; pinned sessions are `ended`. | check_pins_refuse_missing_id |
| R13 | Every non-card scene is settled at its hero, and SSIM(frame at `hero_s`, `hero/<id>.png`) ≥ 0.90. | check_hero_frames_legible |
| R14 | `console_errors = []`; no error state at any hero; `check_secrets.py` passes on every text dump. | check_clean_frames |
| R15 | Isolation: no blocked writes; snapshot counts and tmux sessions unchanged; `promo-admin` snapshot-only. | check_snapshot_isolation |
| R16 | The rail is collapsed at every beat start and hero. | check_rail_collapsed |
| R17 | No hard cut, no flash: on the mp4 at 640×360 every `scdet` score ≤ 10 and every mean-luma step ≤ 8. | check_no_flash_or_hard_cut |
| R18 | The film's first frame is the cover: `out/cover.png` exists at 3840×2160, SSIM(first frame, cover.png) ≥ 0.90, and the cover draws its title lines and both agent chips. | check_cover_opens_the_film |
| R19 | Every beat that starts on `/trace/sessions/:id` is in the `Conversation` view. | check_conversation_only |
| R20 | No plate holds the frame still for more than 2.5 s. | check_no_dead_air |
| R21 | Every Pipeline-tab hero is at `?project=<id>` of `storyboard.project`, and every feature, task and defect it links to belongs to that project (a defect with no feature belongs to none). | check_project_scoped |
| R22 | The cover is up at 0 s, the outline starts by 3.1 s, the first footage by 10 s. | check_opening_pace |
| R23 | The cover names Claude Code and Kimi Code; the outro draws its title, tagline, every ability tile, both agents, `regin serve` and the site. | check_says_what_it_is |
| R24 | Every font face loaded at boot; every Chinese field is drawn in Fusion Pixel; every card field but the outro's command is drawn in a pixel face (Silkscreen or Fusion Pixel). | check_pixel_fonts |
| R25 | Every level card wipes in over the footage with blocks and its robot hops three times; every card followed by footage wipes back out with blocks. | check_level_transitions |
| R26 | The scene after the WATCH card is a session scene with ≥ 3 beats in the Conversation view and ≥ 1 beat in Presentation Mode (`span-present-reader` up); no other scene opens Presentation Mode. | check_watch_level |
| R27 | Outside cards and plate holds, no screencast frame stays on screen longer than 3.0 s. | check_no_still_footage |
| R28 | At no beat start or hero is on-screen text naming the film's own feature (`storyboard.self`, its title) inside the frame. | check_film_does_not_show_itself |
| R29 | In an `en` cut no Chinese is in frame — beneath the cover, at any beat start or hero, or after any visible camera action (a scroll counts what it swept past). Not applicable to `zh`. | check_footage_in_english |
| R30 | A storyboard exporting `x` writes `regin-promo-x.mp4`: H.264 High 1920×1080 30 fps progressive yuv420p, square pixels, AAC-LC ≤ 2 ch ≥ 128 kb/s, video ≥ 5 Mb/s, ≤ 512 MB, the master's duration. | check_x_upload |

## Invariants

- I1 A build never writes to `db/regin.db`, `db/regin_memory.db` or `config/settings*.json`, and never binds :8321 or :5173.
- I2 Every word the overlay draws lives in the storyboard's cards and levels, except the wordmark; every word the camera types into the app lives in its scene's `typed`, and nothing typed is sent. The footage carries no text the app did not render itself — in a translated cut the app renders the replica's rows as read through the glossary, every replaced phrase the glossary's (D18); the overlay's layers are the dim, the block canvas, the card, the pointer and the freeze plate.
- I9 A replayed read is a byte copy of the response the same replica returned for the same URL during the warm-up of this build (as the glossary rewrote it, in a translated cut); nothing is replayed across builds.
- I3 Time is readiness plus declared bars. The recorder has no `waitForTimeout`; its only clock waits are the downbeat wait and the bar hold, both computed from `music.bpm`.
- I4 Nothing under `frontend/promo/out/` is committed. `frontend/promo/music/` (one audio file ≤ 10 MB plus `LICENSE.md`) and `frontend/promo/fonts/` (one woff2 plus its OFL texts) are.
- I5 `git diff --name-only master...HEAD` lists only `frontend/promo/**`. `website/src/content/play.js` and the Silkscreen fonts are read, never changed.
- I6 `frontend/film/**` is untouched.
- I7 The promo server launches no agent process and runs no verify command.
- I8 The seed exists in exactly two places — the storyboard and the manifest.

## Acceptance

```gherkin
Feature: The promo video has the requested shape
  Scenario: runtime streams and coverage
    Given `node promo/build.mjs` completed on the full storyboard
    Then ffprobe shows one 3840x2160 30fps H.264 stream, one AAC stream, no subtitle stream, and a duration between 150 and 300 seconds
    And the manifest's scene routes cover the six Pipeline and Session routes, never the sessions list or the all-defects list, and no other
    And cards run at most 28% of the runtime

  Scenario: it opens on the hook and the outline
    Then the mp4's first frame matches out/cover.png, and the cover asks "现代 AI 工作台，应该长什么样？" and names Claude Code and Kimi Code
    And the outline starts by 3.1 s and lists the six levels in order, and the first footage starts by 10 s

  Scenario: the levels are game screens in Chinese
    Then six level cards LEVEL 1 to LEVEL 6 carry the website's stage words (the five P_STAGES words in order, and WATCH), each wipes in with blocks and its robot hops three times on the map
    And every card draws Chinese, in Fusion Pixel, and every word it draws is the storyboard's or the wordmark

  Scenario: it says what regin is for
    Then the outro draws 给 AI 编程 Agent 的工作台, the four abilities, Claude Code and Kimi Code, regin serve and regin.ccday.top

  Scenario: the Pipeline tabs show only regin
    Then every Pipeline hero is at ?project=<regin's id> and links to no feature, task or defect of another project

  Scenario: WATCH is its own level, the trace shows the conversation only, and the film has no dead air
    Then the scene after the WATCH card has at least 3 Conversation beats and opens Presentation Mode, and no other scene does
    And every beat on the trace page is in the Conversation view
    And no plate holds the frame still for more than 2.5 s, and no footage frame stays up longer than 3.0 s

  Scenario: the film does not show itself
    Then no beat start or hero has text naming the film's own feature inside the frame

  Scenario: no captions, licensed music, mastered audio, on the grid, pixel and dark, seed hidden, legible, rail folded, no flash
    Then check_no_captions, check_music_license_and_fit, check_audio_mastered, check_cards_on_downbeat, check_pixel_theme,
      check_seed_hidden, check_hero_frames_legible, check_rail_collapsed and check_no_flash_or_hard_cut pass

Feature: The English cut for X
  Background:
    Given `node promo/build.mjs --storyboard promo/storyboard.en.json --out promo/out/en` completed

  Scenario: the cards speak English
    Then the cover asks "What should a modern" / "AI workbench look like?" and names Claude Code and Kimi Code
    And no card draws Chinese, every card field but the command is drawn in Silkscreen or Fusion Pixel, and every other check of the Chinese cut passes on promo/out/en

  Scenario: no Chinese reaches the frame
    When the camera looks at the page beneath the cover, at every beat start and hero, and after every scroll, click, dissolve, zoom and typed reply
    Then no look finds Chinese text in frame, a scroll counting what it swept past

  Scenario: the X file meets X's upload rules
    Then promo/out/en/regin-promo-x.mp4 is H.264 High 1920x1080 30fps yuv420p with AAC-LC stereo, its video at 5 Mb/s or more, at most 512 MB, as long as the master

Feature: Recording is safe to run against real data
  Scenario: a missing pin refuses
    Given a storyboard whose feature pin is 999999
    When `node promo/build.mjs --storyboard <that file>` runs
    Then it exits non-zero, stderr names "feature" and 999999, and no mp4 is written

  Scenario: nothing launches nothing writes
    Then no new tmux session exists, the snapshot's counts are unchanged, blocked_writes is empty, and promo-admin is absent from the live db/regin.db

  Scenario: clean frames
    Then console_errors is empty, no error state is visible at any hero moment, and check_secrets.py passes on every out/text/*.txt
```

### Acceptance tests

| Test | Scenario / rule |
|---|---|
| check_runtime_and_streams | R1 |
| check_route_coverage_and_share | R2, R3 |
| check_no_captions | R4, D2 |
| check_music_license_and_fit | R5, D4 |
| check_audio_mastered | R6 |
| check_cards_on_downbeat | R7, D3 |
| check_pixel_theme | R8, D6 |
| check_seed_hidden | R9, D8, I8 |
| check_card_copy | R10, D5, D7, D9 |
| check_pins_refuse_missing_id | R12, D10 |
| check_hero_frames_legible | R13 |
| check_clean_frames | R14 |
| check_snapshot_isolation | R15, D11, I1, I7 |
| check_rail_collapsed | R16 |
| check_no_flash_or_hard_cut | R17, D12 |
| check_cover_opens_the_film | R18, D7 |
| check_conversation_only | R19, D13 |
| check_no_dead_air | R20, D14 |
| check_project_scoped | R21, D6 |
| check_opening_pace | R22 |
| check_says_what_it_is | R23 |
| check_pixel_fonts | R24, D5 |
| check_level_transitions | R25, D12 |
| check_watch_level | R26, D13 |
| check_no_still_footage | R27, D14 |
| check_film_does_not_show_itself | R28, D16 |
| check_footage_in_english | R29, D18 |
| check_x_upload | R30, D19 |
| test_every_slice_merged_back | Work slices |
| operator watches regin-promo-pixel.mp4 | MANUAL: the hook grabs, the outline and level cards read in Chinese, the flow is complete, the transitions read as a game, the track suits a promo, nothing reads like a caption, D9, R11 |

Every `check_*` is a named check in `frontend/promo/check.mjs` and runs as `node promo/check.mjs <name>`.

## Gates (build step)

- `cd frontend && node promo/build.mjs && node promo/check.mjs`: every check PASS.
- `cd frontend && node promo/build.mjs --storyboard promo/storyboard.en.json --out promo/out/en && node promo/check.mjs --out promo/out/en`: every check PASS.
- I5: `git diff --name-only master...HEAD` lists only `frontend/promo/**`; no `.vue`, `frontend/src` or Python change, so no BundleEngine, pytest or radon gate.
- The promo stack lives on :8323/:5175 and is torn down by `build.mjs` on exit, including on failure.
- Build from a shell or a session that is not parked on a question (a parked session raises the `role="alert"` banner R14 refuses).

## Work slices

**Base: `f162/integration`, cut from `master`.** Every slice and every fix merges back into it, and it never merges into
`master`: the music and fonts would grow every clone of the repo (D302). It takes `master` by merging `master` in. No slice makes a schema change.
`promo/storyboard.smoke.json` (cover → outline → overview → level 1 → outro) is the harness's regression fixture.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Harness: music-only, pixel chrome | No narration path; `seed.mjs`; `ctx.beat` with bars and the downbeat wait; the overlay (cards, block canvas, pointer) from F154 tokens with Silkscreen and Fusion Pixel; music bed and mux; `check.mjs`; the smoke storyboard | D1–D6, D8, D11, D12, D14, R4–R9, R12–R18, R20, R22–R25, I1–I4 | check_no_captions, check_music_license_and_fit, check_audio_mastered, check_cards_on_downbeat, check_pixel_theme, check_seed_hidden, check_pins_refuse_missing_id, check_clean_frames, check_snapshot_isolation, check_rail_collapsed, check_no_flash_or_hard_cut, check_cover_opens_the_film, check_opening_pace, check_pixel_fonts | — | — | f162/integration | goal-verified-treenav | cd frontend && node promo/build.mjs --storyboard promo/storyboard.smoke.json --out promo/out/smoke && node promo/check.mjs --out promo/out/smoke check_cards_on_downbeat check_cover_opens_the_film check_opening_pace check_pixel_fonts check_no_flash_or_hard_cut check_no_captions check_seed_hidden |
| S2 | Storyboard and scenes | The full storyboard (levels, pins, warm, frames, open_at, typed, self) and `scenes.mjs`, every scene arriving by a click chain | Storyboard, D3, D7–D10, D13, D15, D16, R1–R3, R7, R9–R11, R13, R16, R19, R21, R26–R28 | check_runtime_and_streams, check_route_coverage_and_share, check_card_copy, check_hero_frames_legible, check_conversation_only, check_project_scoped, check_says_what_it_is, check_level_transitions, check_watch_level, check_no_still_footage, check_film_does_not_show_itself | — | S1 | f162/integration | goal-verified-treenav | cd frontend && node promo/build.mjs && node promo/check.mjs |
| S3 | integrate + verify | S1 and S2 merged back with commits of their own; the full build and every check on the integration branch; I5 and I6 | Work slices, Acceptance, Gates, I5, I6 | test_every_slice_merged_back, every check | — | S1, S2 | f162/integration | goal-verified-treenav | cd frontend && node promo/build.mjs && node promo/check.mjs && git diff --name-only master...HEAD |
| S4 | MANUAL — operator watches the video | Operator only. Watch `frontend/promo/out/regin-promo-pixel.mp4` end to end; each fault is filed as a defect against #162 naming the scene id | Acceptance (MANUAL row), D9, R11 | operator watches regin-promo-pixel.mp4 | — | S3 | f162/integration | — | — |
| S5 | The English cut for X | `storyboard.en.json`; `lang` through build, overlay and checks; the glossary read layer (`lib/i18n.mjs`, `i18n/en.json`) and the in-frame look (`hanInFrame`); `exports: ["x"]` and `exportX`; the SPEC lightbox on image 11 | D5, D7, D17–D19, R10, R24, R29, R30, I2, I9 | check_card_copy, check_pixel_fonts, check_footage_in_english, check_x_upload, every check on both cuts | — | S3 | f162/integration | — | cd frontend && node promo/build.mjs --storyboard promo/storyboard.en.json --out promo/out/en && node promo/check.mjs --out promo/out/en && node promo/build.mjs && node promo/check.mjs |

## Follow-ups (not built)

- A persistent pixel HUD frame around the footage (level code, stage pips) — the viewport would shrink and every SSIM/luma threshold needs re-deriving.
- A Guidance chapter (`/memory`, `/patterns`, `/rules`) with its own pins and scene functions.
- A chiptune generated from the seed by a checked-in synth script, as an alternative bed.
- A 60-second 9:16 cut for WeChat Channels and Douyin.
- A 2:20 cut, should an X account's uploader still enforce the help centre's old 140 s cap (its own storyboard; every level's bars).
- The glossary is keyed to today's data: a new Chinese title that reaches the frame fails R29 until a phrase for it is added.
- Sound effects on the hops and wipes (the bed is music only today).
- The session's follow-up chat box on screen: record a live, steerable regin session outside the replica (needs its own isolation story).
- App issues the camera works around, to file against their own features: the ledger's Graph link drops the project scope; a Closed
  session's conversation still shows "Follow latest" / "Jump to live", and when its catch-up `/subtrees` read is not the warm-up's
  URL (its `known=` watermark depends on the push stream's timing) it counts the deep-loaded spans as "99+" new.


