# Feature #127 — regin promo video (中文, 3–5 min, Pipeline + Session)

## Intent
A 3–5 minute promo video for regin, **narrated and subtitled in Chinese**, that spends its time on the **Pipeline** pages and the **Session** pages. It has to show what regin is for: an AI coding agent's work is specced, sliced into tasks, run, and verified, and every defect is attributed back to its feature. It also has to show that every session behind that work can be opened and read, tool call by tool call.

The story is regin building itself. The footage is real: feature #125, its slice tasks, its defects and its goal-verified sessions, recorded from a snapshot of the live database. The video is **produced by a checked-in, re-runnable script**: a storyboard, a Playwright recorder, TTS and ffmpeg. When the UI changes, the video is regenerated, not re-shot by hand.

Non-goals:
- publishing or uploading (Bilibili, WeChat Channels, …). That is the operator's act, after they have watched the file;
- background music, a human voice-over, an English cut (see Follow-ups);
- localizing the product UI. The UI stays English; only narration, subtitles and title cards are Chinese;
- changing any product page so it looks better on camera. A page that renders wrong in the recording is a defect against that page's own feature, not work done here;
- a synthetic demo dataset.

## Decisions (operator approves with the spec)

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | **Scripted production.** A Playwright library script (1.59.1, already in `frontend/node_modules`) drives the real SPA and records it. macOS `say` voices the narration, and ffmpeg (installed, with libx264, native AAC and `ssim`) muxes the result. | (a) Manual screen recording. (b) Remotion | (a) is not reproducible, so every UI change means a re-shoot. (b) adds a React video dependency, and it composes a picture of the UI rather than recording the real one. |
| D2 | **Data = a snapshot of the live `db/regin.db`**, taken with SQLite's online backup into a scratch dir. It is served by its own `regin serve` on **:8323** and vite on **:5175**, with all four stores redirected the way `frontend/e2e-env.js` does. A `promo-admin` user is created in the snapshot only. | (a) Synthetic seed data. (b) Record against the live :8321 | (a) A fabricated span tree looks fabricated, and seeding a realistic one is a project of its own. (b) Writes would land in the real DB, and the sessions list is topped by the recording agent's own live session (lesson d9aebb65). |
| D3 | **`regin serve --replica`: a read-only server with no boot side effects.** This is the feature's only product change. With `--replica`, `cmd_serve` starts neither the workflow watcher nor the pending reaper. `create_app(replica=True)` skips `_reap_orphaned_agent_runs()`, which covers the SDK reap, the bridge tmux reap and `reap_tmux_runs_at_boot`. A `before_request` hook answers every non-GET `/api/*` request with **403 `{"error": "replica"}`**, except `POST /api/auth/login` and the realtime handshake (`POST /api/auth/stream-ticket`, `POST /api/notifications/stream/topics`). The handshake's ticket and topic set live in server memory only, and the SPA opens the stream on every page. Refusing it would put a reconnect loop into every recorded frame. The same hook also refuses the pane-terminal WebSocket (`GET /api/sessions/<id>/terminal`, endpoint `bridge_terminal.api_session_terminal`) with the same 403 whatever the method: its upgrade is a GET, but it types into live tmux panes. The flag is a process argument, not a setting, so it has no SettingsView row. As defence in depth, the recorder's browser also aborts non-GET `/api` requests outside that same allowlist (`context.route`) and logs each one. | (a) Clear the snapshot's deferred-fire markers before serving. (b) Trust the storyboard never to click a write verb | The unconditional boot pass (`web/app.py:180` → `lib/tasks/runs.py:590-600`) settles tmux runs it finds gone, resumes orphaned verifies and fires deferred tasks. Settling fires dependents. So serving **any** copy of a DB with work in flight launches real agent runs and verify commands in the real repo. The build's own task attempt is always in flight when it snapshots. (a) misses the settle→fire path. (b) leaves the boot pass. |
| D4 | **Showcase entities are pinned by id** in the storyboard: one feature (default #125), one of its slice tasks, one of its defects, and ≥1 **ended** session attributed to it. They are chosen at build time from the snapshot. When a pinned id does not resolve (`GET` 404), the build exits non-zero and names the id. No scene ever falls back to "the first row". | Pick the first row of each list | The first sessions row is whatever ran last (lesson d9aebb65). Session rows are located by their `aria-label="Copy session id <id>"` anchor, not by list position or a `?q=` param (lesson 6971b0ad). |
| D5 | **Narration drives timing.** Each line is synthesized first and its duration is measured with ffprobe. A scene runs its actions *inside* its lines: `ctx.line(i, fn)` shows the subtitle, logs the start, and resolves when both `fn` and the line's audio duration are done. The mux places each audio clip at its logged start. | Hand-tuned sleeps per scene | Hand-tuned sleeps drift the moment a line is reworded. Here, rewording a line re-times its scene with no edit to the scene code. |
| D6 | **Subtitles are drawn in-page** by an injected overlay (PingFang SC, bottom band), so they are part of every frame, and **nowhere else**: the mp4 carries no subtitle track. The SRT the checks read (and an uploader may attach) is `captions.srt`, never the mp4's own name. The band is opaque. Within a scene, a new line swaps only its text, so consecutive lines never blink the band off and on. | (a) Burn in with ffmpeg `subtitles=`. (b) Also mux a `mov_text` track and a same-named `.srt` | (a) The installed ffmpeg has no libass. (b) Players show a default soft track, and auto-load a sidecar named like the video, on top of the burned-in band, so every line is read twice (defect #225). |
| D7 | **Capture: CDP `Page.startScreencast`** (JPEG q95, one frame per repaint, each stamped with its wall-clock time) at viewport **1280×720 × `deviceScaleFactor` 3**, with chromium launched with `--force-device-scale-factor=3`: 3840×2160 device-pixel frames, the UI at 1.5× its desktop size in a 1080p view. Mastered as **4K** H.264 (high@5.1, CRF 16) at 30 fps CFR. The recorder sets `localStorage.regin_theme = 'dark'` and **`regin_sidebar_collapsed = '1'`**: the rail stays folded to its icon column for the whole film. Legibility is a bar, not a mechanism (R8). | (a) Playwright `recordVideo`. (b) 1536×864 × 1.25 → 1080p with the rail expanded | (a) It encodes at CSS-pixel size whatever the deviceScaleFactor, with a VP8 bitrate cap on top. (b) The #225 cut: small UI text in a soft 1080p frame, and an expanded rail spending a quarter of every frame on navigation. |
| D8 | **Voice:** `say -v Tingting` (zh_CN) by default. The storyboard's `voice` and `rate` fields override it, e.g. `"Flo (Chinese (China mainland))"`. A line may carry `speak` to fix how a term is pronounced; TTS reads `speak ?? text`. | edge-tts neural voices | It needs network access, an unofficial endpoint and a new pip dependency. Listed as a follow-up. |
| D9 | **Location:** everything except D3 lives in `frontend/promo/`, which resolves `playwright` from `frontend/node_modules` and mirrors `frontend/scripts/e2e-server.mjs` provisioning. Output goes to `frontend/promo/out/`, which is gitignored. The mp4 is never committed. | `scripts/promo/` at the repo root | That would need its own module resolution to reach Playwright. |
| D10 | **Narration states no figure read from the data**: no counts, dollars, durations or ids. The screen shows them. | Say "$31.57 across 11 sessions" | The snapshot is re-taken on every build, so any spoken number goes stale on the next attribution. |
| D11 | **One document, dissolved.** The film is one SPA document. The app loads once, before t0, beneath the opening card, which is an in-page layer and fades away to reveal it. After that, every change of view is a **visible click on a real link** (`ctx.follow`) made beneath a **freeze plate**: a still of the frame as shown, subtitle and pointer included, laid over everything. Putting it up changes no pixel. Once the new view has settled it dissolves away (560 ms). Section jumps too long to glide (`ctx.jumpTo`) use the same plate. Scrolls glide on an eased curve. The camera may zoom, via a transform on `<body>`. The scenes follow one chain of links (feature → defect → feature → slice task → its run → its session), so each scene starts where the last left off. | (a) `page.goto` per scene. (b) Hide the overlay to capture a clean plate, and drop the frames recorded meanwhile by their timestamps. (c) Cross-fade in ffmpeg | (a) The #225 cut: every full navigation painted a pure-white frame (6 flashes, scene score ~90). (b) A frame drawn before the overlay came back can be stamped after it: the band check caught 3 one-frame subtitle blinks. (c) The recording is continuous, so there is no cut point to fade across without first cutting the navigation out. |

## Shape

```ts
// frontend/promo/storyboard.zh.json — the ONLY place narration text lives (I2)
interface Storyboard {
  title: string                    // title card, e.g. "regin：AI 编码代理的流水线与会话追踪"
  voice: string                    // D8, default "Tingting"
  rate?: number                    // say -r
  pins: { feature: number; task: number; defect: number; sessions: string[] }   // D4
  scenes: Scene[]
}
interface Scene {
  id: string                       // unique; one entry in scenes.mjs per non-card id
  surface: 'card' | 'pipeline' | 'session'
  route: string | null             // null for a card; may use {feature} {task} {defect} {session0}
  hero: string | null              // selector visible at the hero moment; null for a card
  lines: Line[]
}
interface Line { id: string; text: string; speak?: string }   // text ≤ 60 chars = one subtitle

// frontend/promo/scenes.mjs — one function per non-card scene id
type SceneFn = (ctx: {
  pins: Storyboard['pins']
  route: string | null             // the scene's route with its pins filled in
  line(i: number, during?: () => Promise<void>): Promise<void>   // D5
  hero(): Promise<void>            // waits for a settled page, then lossless screenshot + innerText dump + hero_s + visible error states + rail state, once per non-card scene
  aim(selector: string): Promise<Locator>        // eased scroll into view if needed, then glides the pointer to the target
  point(selector: string): Promise<void>         // aim + click, for a small local change (a menu, a toggle)
  follow(selector: string, ready: string, beneath?: () => Promise<void>): Promise<void>   // aim + click beneath a freeze plate; waits for `ready` and a settled page, runs `beneath` (a fix-up the camera should not show) and settles again; dissolves (D11)
  scrollTo(selector: string, block?: 'start' | 'center', opts?: { instant?: boolean }): Promise<void>   // eased (or, beneath a plate, instant) scroll of the nearest scrolling ancestor
  jumpTo(selector: string, block?: 'start' | 'center'): Promise<void>     // instant scroll beneath a freeze plate, dissolved
  zoom(selector: string | null, k?: number, opts?: { left?: boolean }): Promise<void>   // camera zoom (the pointer rides along); null returns to the full frame
  settle(): Promise<void>          // no /api request in flight and 12 consecutive frames with no element/attribute change (text-only ticks excepted), scroll or finite animation
}) => Promise<void>
// A card scene has no function; it may carry `title?` (default Storyboard.title) and `caption?`.

// frontend/promo/out/  (gitignored)
interface Manifest {
  storyboard: string; pins: Storyboard['pins']
  scenes: { id: string; route: string | null; surface: string; start_s: number; end_s: number; hero_s: number | null
            visited: string[]              // pathnames the scene was on or navigated to (R2 is checked against these)
            hero_errors: string[]          // error/empty states visible at the hero moment (R9)
            rail_at_hero: 'collapsed' | 'expanded' | 'absent' | null   // R14
            lines: { id: string; start_s: number; dur_s: number
                     shown_until: number                              // the band holds this line until then (≥ start_s + dur_s when `during` outran the audio)
                     rail: 'collapsed' | 'expanded' | 'absent'         // R14, sampled at the line's start
                     subtitle_layers: number                          // R13
                     band: { x: number; y: number; w: number; h: number } }[] }[]   // the subtitle band's CSS box (R13)
  console_errors: string[]         // page 'console' type=error + 'pageerror', whole recording
  blocked_writes: string[]         // "METHOD /api/..." aborted by D3's route guard
  snapshot_counts: { before: Counts; after: Counts }   // Counts = { sessions, agent_tasks, agent_task_runs }
  tmux_sessions: { before: string[]; after: string[] } // regin's tmux server, `tmux ls`
  stores: { snapshot: string; live: string; tmux_socket: string }
  capture: { method: 'cdp-screencast'; frames: number; size: { width: 3840; height: 2160 } }
}
//   scratch/ (the snapshot + its redirected stores) · voice/<line-id>.aiff (say, trimmed of edge silence)
//   hero/<scene-id>.png · text/<scene-id>.txt · frames/*.jpg + frames/frames.ffconcat
//   captions.srt · regin-promo-zh.mp4   (H.264 + AAC, no subtitle track, 3840×2160, 30 fps CFR)
```

Commands (from `frontend/`):
```
node promo/build.mjs [--storyboard promo/storyboard.zh.json] [--out <dir>]   # provision → pins → tts → record → mux; tears its servers down on exit
node promo/check.mjs [--out <dir>] [<check-name> …]            # no args = every check; prints PASS/FAIL per check, exit 1 on any FAIL
```

### Storyboard (scene order and what each must land)
The beats are the required content. The storyboard file owns the final wording (I2), and each line must be true of the frame it plays over. Every scene after the first is **arrived at by a click in the scene before it** (D11). The built cut runs ≈ 211 s.

| # | id | surface | route | Arrives by | On screen | Beat (中文) | ≈s |
|---|---|---|---|---|---|---|---|
| 1 | intro | card | — | — (the app loads beneath it) | Title card: regin + subtitle | AI 编码代理越来越快——可它做了什么、做对了没有、花了多少？regin 把这三件事摆到明处。 | 13 |
| 2 | overview | pipeline | `/overview` | the card fading away | The loop (tasks · features · defects); the Run next and Needs a decision panels, which render whatever the snapshot holds (Running now shows only while a task runs, so no beat depends on it) | 从流水线总览出发，三本账一屏看全；下一个该跑的、等人拍板的也在这里。 | 15 |
| 3 | queue | pipeline | `/tasks` | the **Tasks** tab | Tasks grouped by next step; the Graph view control (always present, whatever the snapshot holds) | 每一组都告诉你接下来该做什么；依赖画成了图，依赖一满足就自动开跑。任务来自功能的规格。 | 16 |
| 4 | features | pipeline | `/features` | the **Features** tab | Features table: stage, tasks, rounds, defects; the Escaped stage chip | 功能有阶段、进度、返工和缺陷；发布后才发现的记为逃逸。 | 16 |
| 5 | defects | pipeline | `/defects` → `/defects/{defect}` | the triage rail's **All defects →** | The defect list; the pinned defect opened, zoomed on its missing check | 每条缺陷归属到功能；关闭时必须写明哪条检查本该抓住它。 | 20 |
| 6 | ledger | pipeline | `/features/{feature}` | the defect's feature link | Stage line, figures (zoomed), the spec's Acceptance tests, the Slices table | 功能的账本：状态、成本与工时按真实记录汇总；验收项就是测试名；切片表生成任务和依赖。 | 28 |
| 7 | task | pipeline | `/tasks/{task}` | the slice table's task link | Delivers, 验收测试名, Run history with "verify passed" | 任务交付什么、依据哪条规格；做没做完由测试说了算；每次尝试都留档。 | 22 |
| 8 | trace | session | `/trace/sessions/{session0}` | the run's **open the session trace** | Header facts (zoomed), vitals, conversation with a tool failure, Agents roster (goal-verifier), Timeline, a tool call's span details (duration, tokens, the content it read) | 会话头部是全部事实；对话里每一次工具调用、每一个子代理；主代理构建，验证者独立复核。 | 42 |
| 9 | messages | session | `/trace/sessions/{session0}` (Messages) | the view menu | The session's `send_to_user` summary / progress / result cards. No lesson message is claimed | 代理边做边汇报；这些汇报也进入收件箱。 | 12 |
| 10 | sessions | session | `/trace/sessions` | the header's **‹ Sessions** | The sessions list, range set to All time on camera; the pinned row | 每一个会话都记在这里；刚才那一次也在其中。 | 18 |
| 11 | outro | card | — | the card fading in | Title card + `regin serve` | regin——让 AI 编码代理的每一步，都看得见、算得清、验得过。 | 8 |

## Rules

| # | Rule | Checked by |
|---|---|---|
| R1 | Final mp4: exactly one H.264 video stream at **3840×2160** and 30 fps, one AAC audio stream, and **no subtitle stream**. Duration is **180–300 s** (ffprobe). | check_runtime_and_streams |
| R2 | The scene routes cover all nine of: `/overview`, `/features`, `/features/:id`, `/tasks`, `/tasks/:id`, `/defects`, `/defects/:id`, `/trace/sessions`, `/trace/sessions/:id`. No scene visits a route outside Pipeline and Session, except cards (`route: null`). | check_route_coverage_and_share |
| R3 | Cards total ≤ 30 s. Pipeline scenes take ≥ 45% of runtime and Session scenes ≥ 25%. | check_route_coverage_and_share |
| R4 | Every line contains a CJK character, and CJK characters make up ≥ 75% of the letters across the whole script (CJK + Latin). No line exceeds 60 characters. No line states a figure read from the data (D10). | check_narration_is_chinese (the D10 part is reviewed by the verifier, since it cannot be machine-proved) |
| R5 | `captions.srt` holds one cue per line, and its start and end equal the manifest placement. Each cue start is within 0.3 s of a speech onset detected on the final audio track (`silencedetect` end). No two lines overlap. | check_subtitles_match_audio |
| R6 | Audio is loudness-normalized: integrated loudness between −18 and −14 LUFS (`ebur128`). | check_audio_loudness |
| R7 | A pin that does not resolve makes the build exit non-zero before recording. stderr names the pin and the id, and no mp4 is written (D4). | check_pins_refuse_missing_id |
| R8 | For every non-card scene, SSIM(the video frame at `hero_s`, `hero/<id>.png`) ≥ **0.90** (D7). | check_hero_frames_legible |
| R9 | The recording is clean. `console_errors` is `[]`. At every hero moment, no error or empty state is visible (`[data-testid=trace-load-error]`, the not-found view, a refusal line). `.venv/bin/python scripts/check_secrets.py out/text/*.txt` exits 0. | check_clean_frames |
| R10 | Isolation. `blocked_writes` is `[]`. `snapshot_counts.before == after`. The `tmux_sessions` sets are equal. `promo-admin` exists in the snapshot's `users` table and is absent from the live `db/regin.db` (D2, D3). | check_snapshot_isolation |
| R11 | Every click is preceded by a visible pointer move to its target (`ctx.aim`). The pointer's hotspot (the arrow's tip) lies inside the target's box at click time, and the recorder refuses the click otherwise. Every change of view is such a click, dissolved (D11); no scene navigates by URL. | recorder-enforced; the verifier reviews it |
| R12 | Pinned sessions are **ended**: none carries a live badge in the snapshot. | check_pins_refuse_missing_id |
| R13 | Every line is shown **exactly once**. The mp4 has no subtitle stream. `out/` holds no subtitle file named like the mp4. Every line was drawn with exactly one band. The band's box, read from **every** frame the line holds the band (`start_s` + 0.25 s to `shown_until`, so a dissolve that outruns the audio is covered; 30 fps, grey, half the CSS size), stays within a mean difference of 8 grey levels of the line's first frame (the band is opaque, so a steady one reads ~0 whatever moves behind it). | check_subtitles_burned_once |
| R14 | The rail is collapsed (`aside.sidebar.is-collapsed`) at every line start and every hero moment of every non-card scene. | check_rail_collapsed |
| R15 | No hard cut and no flash. On the final mp4 (scaled to 640×360), every frame's `scdet` score is ≤ 10, and every frame-to-frame step in mean luma (`signalstats` YAVG) is ≤ 8. A white frame between two dark pages scores ~90 and steps ~200. | check_no_flash_or_hard_cut |

## Invariants
- I1 A build never writes to `db/regin.db`, `db/regin_memory.db` or `config/settings*.json`, and never binds :8321 or :5173.
- I2 Narration text lives only in the storyboard. Audio, the in-page band and `captions.srt` are all derived from it.
- I3 Scene length is set by readiness and by measured narration duration (D5). Readiness means an awaited selector plus a settled page: no `/api` request in flight, and 12 consecutive frames with no change to elements or attributes, no scroll and no finite animation. Text-only changes do not count, because a live clock such as "waiting 7m 3s" ticks for as long as the page is open. No scene contains a hand-tuned `waitForTimeout`. The dissolve (560 ms), card fade (900 ms), camera zoom (1100 ms) and distance-scaled glides and scrolls are animation lengths, not waits.
- I4 Nothing under `frontend/promo/out/` is committed.
- I5 The only product change is D3. `git diff --name-only <base>...HEAD` lists only `frontend/promo/**`, `.gitignore`, `cli/commands/server.py`, `web/app.py` and `tests/web/test_replica.py`. The replica flag changes nothing when it is absent.
- I6 The promo server launches no agent process and runs no verify command (D3, R10).
- I7 A server started without `--replica` behaves exactly as before: the boot pass runs and writes are accepted.

## Acceptance

```gherkin
Feature: The promo video has the requested shape
  Scenario: runtime, streams, coverage
    Given `node promo/build.mjs` completed on the full storyboard
    Then ffprobe shows one 3840x2160 30fps H.264 stream, one AAC stream, no subtitle stream, and a duration between 180 and 300 seconds
    And the manifest's scene routes cover the nine Pipeline and Session routes
    And the cards total at most 30 seconds

  Scenario: Chinese narration that matches its subtitles
    Then every storyboard line contains CJK text and the script is at least 75% CJK letters
    And captions.srt has one cue per line, each starting within 0.3s of a speech onset on the final audio

  Scenario: each line is read once
    Then the mp4 carries no subtitle track and out/ holds no subtitle file named like the mp4
    And every line was drawn with one band, and in every frame of the line the band's box differs from its first frame by at most 8 grey levels

  Scenario: the rail stays folded
    Then at every line start and hero moment of every non-card scene the rail is collapsed

  Scenario: no cut, no flash
    Then no frame of the mp4 scores above 10 on scdet, and no frame steps the mean luma by more than 8

  Scenario: legible frames
    Then for every non-card scene the video frame at hero_s has SSIM >= 0.90 against hero/<id>.png

Feature: Recording is safe to run against real data
  Scenario: a missing pin refuses
    Given a storyboard whose feature pin is 999999
    When `node promo/build.mjs --storyboard <that file>` runs
    Then it exits non-zero, stderr names "feature" and 999999, and no mp4 is written

  Scenario: nothing launches, nothing writes
    Given the snapshot holds a task run with a deferred-fire marker
    When the build completes
    Then no new tmux session exists on regin's tmux server, the snapshot's sessions/agent_tasks/agent_task_runs counts are unchanged,
      blocked_writes is empty, and promo-admin is absent from the live db/regin.db

  Scenario: the replica server is read-only
    Given `regin serve --replica` on a DB holding an open tmux task attempt whose session is gone and a deferred-fire marker
    Then startup settles nothing and fires nothing
    And POST /api/tasks/<id>/run answers 403 {"error": "replica"}, while POST /api/auth/login answers 200
    And the same DB served without --replica settles the gone attempt at boot (I7)

  Scenario: clean frames
    Then console_errors is empty, no error state is visible at any hero moment, and check_secrets.py passes on every out/text/*.txt
```

### Acceptance tests

| Test | Scenario / rule |
|---|---|
| check_runtime_and_streams | R1 |
| check_route_coverage_and_share | R2, R3 |
| check_narration_is_chinese | R4 |
| check_subtitles_match_audio | R5 |
| check_audio_loudness | R6 |
| check_pins_refuse_missing_id | R7, R12, D4 |
| check_hero_frames_legible | R8, D7 |
| check_clean_frames | R9 |
| check_subtitles_burned_once | R13, D6 |
| check_rail_collapsed | R14, D7 |
| check_no_flash_or_hard_cut | R15, D11 |
| check_snapshot_isolation | R10, D2, D3, I1, I6 |
| test_replica_skips_boot_side_effects | D3, I6 (`tests/web/test_replica.py`: SDK, bridge and tmux reaps and `fire_deferred` are not called; the watcher and pending-reaper threads are not started) |
| test_replica_refuses_writes_but_allows_login | D3 (non-GET `/api/*` → 403 `replica`; login → 200; GET → 200) |
| test_replica_allows_the_realtime_handshake | D3 (stream-ticket → 200; stream topics not refused) |
| test_replica_refuses_the_pane_terminal | D3 (terminal WS upgrade → 403 `replica`; without `--replica` it reaches the handler) |
| test_serve_without_replica_unchanged | I7 |
| test_every_slice_merged_back | Tail |
| test_full_suite_passes | Gates (S1 touches Python) |
| operator watches regin-promo-zh.mp4 | MANUAL: pacing, pronunciation, D10 |

Every `check_*` is a named check in `frontend/promo/check.mjs` and runs as `node promo/check.mjs <name>`.

## Gates (build step)
- `cd frontend && node promo/build.mjs && node promo/check.mjs`: every check PASS. A slice runs the checks it owns by name.
- S1 only: `.venv/bin/python -m pytest -q -n auto --dist=loadfile tests/web tests/tasks`, with radon ≥ C on `web/app.py` and `cli/commands/server.py`.
- I5: `git diff --name-only c8fc63e4...HEAD` touches nothing outside the paths I5 names. No `.vue` or `frontend/src` file changes, so no BundleEngine or product Playwright gate applies.
- No slice restarts :8321. The promo stack lives on :8323/:5175 and is torn down by `build.mjs` on exit, including on failure.

## Work slices

**Base: `f125/integration`** (no separate `f127/integration` was cut; every slice merges back into `f125/integration`). The recording shows the UI of the branch it runs on, and #125 carries the newest Pipeline pages.

No slice makes a schema change, so there is no migrate step. S2's provisioner serves with `--replica`, so it depends on S1. S3 fills the harness S2 builds. S2 proves the chain on a two-scene smoke storyboard, `promo/storyboard.smoke.json`, which stays as the harness's regression fixture.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Replica server | `regin serve --replica` → `create_app(replica=True)`: skip `_reap_orphaned_agent_runs()`, the workflow watcher and the pending reaper; a `before_request` guard answers non-GET `/api/*` except `POST /api/auth/login`, and the pane-terminal WebSocket, with 403 `{"error":"replica"}`; no behaviour change without the flag | D3, I6, I7 | test_replica_skips_boot_side_effects, test_replica_refuses_writes_but_allows_login, test_replica_refuses_the_pane_terminal, test_serve_without_replica_unchanged | — | — | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/web tests/tasks |
| S2 | Promo harness | `frontend/promo/`: provisioner (online-backup snapshot, four redirected stores, `promo-admin`, `serve --replica` on :8323 + vite on :5175, teardown on exit including failure); `say` TTS per line with ffprobe durations; recorder (1280×720 × dsf 3 dark context with the rail collapsed, one SPA document, the in-page layer — card, subtitle band, pointer, freeze plate — `ctx.line`/`hero`/`aim`/`point`/`follow`/`scrollTo`/`jumpTo`/`zoom`, settle-by-frames, non-GET route guard, console capture, pin resolution and refusal, manifest); mux (placed clips, loudnorm, 4K H.264 + AAC, no subtitle track, `captions.srt`); `check.mjs` with all twelve checks; `storyboard.smoke.json` (intro card + overview); `.gitignore` entry for `out/` | D1, D2, D4–D9, D11, R5–R15, I1–I4, I6, Shape | check_subtitles_match_audio, check_audio_loudness, check_pins_refuse_missing_id, check_hero_frames_legible, check_clean_frames, check_snapshot_isolation | — | S1 | f125/integration | goal-verified-treenav | cd frontend && node promo/build.mjs --storyboard promo/storyboard.smoke.json && node promo/check.mjs check_subtitles_match_audio check_audio_loudness check_pins_refuse_missing_id check_hero_frames_legible check_clean_frames check_snapshot_isolation |
| S3 | Storyboard and scenes | `storyboard.zh.json`: all 11 scenes, final Chinese narration (with `speak` overrides where `say` mispronounces a term), pins chosen from the snapshot (feature #125 unless it lacks an ended session); `scenes.mjs` actions for all nine non-card scenes, each arriving by a click in the one before (D11), tuned until the runtime lands in 180–300 s; `out/regin-promo-zh.mp4` built | Storyboard, D4, D5, D10, R1–R4, R9, R11 | check_runtime_and_streams, check_route_coverage_and_share, check_narration_is_chinese, check_clean_frames, check_hero_frames_legible | — | S2 | f125/integration | goal-verified-treenav | cd frontend && node promo/build.mjs && node promo/check.mjs |
| S4 | integrate + verify | Check that S1–S3 are all merged back into `f125/integration` and none landed zero commits. On the integration branch, run `.venv/bin/python -m pytest -q -n auto --dist=loadfile` and `cd frontend && node promo/build.mjs && node promo/check.mjs`. Report the mp4 path, its sha256, its duration and the per-check PASS lines. Confirm I5 with `git diff --name-only`. | Work slices, Acceptance, Gates, I5 | test_every_slice_merged_back, test_full_suite_passes, check_runtime_and_streams, check_snapshot_isolation | — | S1, S2, S3 | f125/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile && cd frontend && node promo/build.mjs && node promo/check.mjs |
| S5 | MANUAL — operator watches the video | Operator only, never armed. Watch `frontend/promo/out/regin-promo-zh.mp4` end to end. Check pacing, how the voice pronounces terms, and that no line states a stale figure (D10). Each fault is filed as a defect against #127, naming the scene id. Then mark done | Acceptance (MANUAL row), D8, D10 | operator watches regin-promo-zh.mp4 | — | S4 | f125/integration | — | — |

## Follow-ups (not built)
- A neural zh-CN voice (edge-tts / a cloud TTS) or a human voice-over, dropped in per line id.
- Licensed background music, ducked under the narration.
- An English cut from a second storyboard (`storyboard.en.json`). The harness is already language-agnostic apart from R4.
- A 60-second vertical (9:16) cut for WeChat Channels and Douyin.
- The snapshot shows whatever is in flight when it is taken: the build's own task attempt (its title under Running now), and a live agent session parked on a question. The latter raises the blocker banner (`role="alert"`), which `check_clean_frames` refuses. That includes the session running the build, when it has ended a turn on suggested next steps. Build from a shell, or from a session that is not parked.





