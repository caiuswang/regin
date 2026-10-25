# F154 — regin website redesign, game-pixel style

## Intent

Rebuild the public website (`website/`, Vue 3 + Vite, live at regin.ccday.top) to match the Claude Design
prototype **1:1 in layout, palette, type and copy**: a black, game-pixel site whose landing page is "World 1"
(four levels, one per layer of the loop, plus a playable Level 0) and whose docs are a "World map" of levels
and reference pages. The prototype is a picture, not a program: where it lacks something the site needs
(a page it never drew, a state it never showed, a phone width it never checked), this spec says in words
what to build, and that text is as binding as the pictures.

Non-goals:
- No change to the operator dashboard (`frontend/`). The website keeps sharing no code with it.
- No light theme. The prototype is dark-only; the current light/dark toggle is removed (decision, see R4).
- No CMS, no hosted backend, no analytics. Copy stays in the repo.
- The prototype's design-tool prop `scanlines` (default off) and the `heroShot` prop (default `overview`)
  are not built as user-visible switches; the site ships their defaults.

## Source of truth

Design project: https://claude.ai/design/p/f3700e0b-c14f-4eb2-a2b2-628fefa6f173 — read with the
`DesignSync` tool (`get_file`, projectId `f3700e0b-c14f-4eb2-a2b2-628fefa6f173`).

| File | What it is |
|---|---|
| `Regin Landing.dc.html` | Landing page: template (inline styles = the exact CSS) + `<script data-dc-script>` logic for Level 0 and the hero scene |
| `Regin Docs.dc.html` | Docs shell + the fully-drawn Pipeline page + `CONTENT` (block lists) for every other docs page |
| `assets/*.webp`, `assets/taskboard.png` | The 16 dashboard screenshots the pages show (2880 px wide, Pixel theme, dark) |
| `support.js` | dc-runtime (React host for the prototype). Not shipped; only needed to render the prototype |

**Transfer cap:** `get_file` returns at most 256 KiB of base64, so `assets/taskboard.png` and
`assets/memory.webp` arrive truncated (196 608 bytes; the lower part of the image is black). They are recaptured
from the live dashboard (S1). The other 14 assets decode whole and are used as delivered.

### Screenshots of the prototype (attached to this feature; `regin feature image 154 <N>`)

Rendered from the prototype at 1440 × 900 (desktop) and 390 × 844 (phone) with Playwright; no console errors.

| N | Shows | Read it for |
|---|---|---|
| 1 | ![](image:1) Landing — header + Hero (HUD bar, headline, CTAs, quick-start box, hero shot, robot scene, four loop tiles) | Hero layout, header, HUD bar pattern |
| 2 | ![](image:2) Landing — Level 0 "Play the loop", six frames: A initial, B NEW FEATURE done, C TASKS done, D GRAPH done, E DEFECTS open, F SHIP done | Every visible state of the Level 0 widget (see state machine) |
| 3 | ![](image:3) Landing — Level 1-1 Workbench and Level 1-2 Runs | The "level" section pattern: HUD bar, text column (h2, lead, terminal `pre`, caption, link), framed shot + figcaption. *The task-board shot is black below its top strip only because of the transfer cap* |
| 4 | ![](image:4) Landing — Level 1-3 Observability (stat tiles + cost meter), Level 1-4 Guidance (rule-check `pre`), the "World 1 clear" CTA and the footer | Stat tiles, meter, CTA box with green border, footer |
| 5 | ![](image:5) Landing at 390 px, full page in three strips | Phone reflow, and the phone defects listed under Gaps |
| 6 | ![](image:6) Docs — Pipeline page, top half: header with DOCS badge, World-map aside, page card (HUD, title, lead, STAGE SELECT chips), "The loop" table, worked-example facts, Stage 1–2, slice table | Docs shell + the Pipeline page |
| 7 | ![](image:7) Docs — Pipeline page, bottom half: task graph shot, Stage 3–5, ledger `pre`, Features tab + TIP, "Where to find it", PREV/NEXT | Rest of the Pipeline page, PREV/NEXT nav |
| 8 | ![](image:8) Docs — Getting Started, full page | The generic docs page: numbered h2 sections and every block type (p, h3, code, WARN note, shot, table) |
| 9 | ![](image:9) Docs — first screen of Runs, Dashboard, Guidance, Architecture, Configuration, CLI, Topic Wikis, plus a page foot | HUD label/code per page, leads, STAGE SELECT per page |
| 10 | ![](image:10) Docs at 390 px — Pipeline (three strips) and Getting Started (top) | Phone docs: aside stacks above the page; tables clip (a Gap) |

## Fidelity rule

| The prototype… | Then the build… |
|---|---|
| draws it (layout, spacing, borders, shadows, colours, fonts, copy) | copies it 1:1; the inline `style` attributes are the CSS to port (as classes, not inline) |
| draws it with `style-hover` / `style-active` / `style-focus` | implements that state exactly as written in the attribute |
| shows a dashboard screenshot | uses that asset (or its recapture, for the two truncated ones) |
| shows a number (109 features, $140.43 …) | prints the same number; these are dated snapshots, not live data |
| draws a page whose content block list (`CONTENT[...]`) differs from today's `website/src/views/*.vue` | uses the prototype's text; anything today's view has that the prototype dropped is dropped |
| does not draw it (see Gaps) | builds what the Gaps table says |
| draws something that breaks at 390 px (see Gaps) | fixes it as the Gaps table says; desktop stays 1:1 |

## Design tokens (from the prototype's inline styles)

```ts
type Palette = {
  bg: '#000'; panel: '#050605'; panelRaised: '#0c0d0c';
  rule: '#20221f'; edge: '#333733';                 // 2px borders everywhere
  ink: '#f6f8f6'; body: '#c3c8c4'; soft: '#d5ddd7'; muted: '#9aa39d'; dim: '#6d766f';
  green: '#2bde5e'; greenHover: '#57e77f'; greenDeep: '#15803d'; greenWash: '#10311d'; greenInk: '#052912';
  gold: '#e8cb7b';                                  // warnings, running, bugs, WARN notes
}
type Fonts = {
  display: 'Silkscreen';                 // 400/700 — HUD labels, kickers, keys, codes, figcaptions, logo
  body: 'Atkinson Hyperlegible Next';    // 16px / 1.6; h1/h2 800
  mono: 'JetBrains Mono';                // pre, code
}
type Chrome = {
  border: '2px solid'; radius: 0;
  shotShadow: '8px 8px 0 #10311d';       // every framed screenshot and the Level 0 screen
  keyShadow: '4px 4px 0 #15803d';        // primary key; :active → translate(4px,4px) + shadow 0
  logo: '22px green square, inset -4px -4px 0 #15803d';
  maxWidth: 1320; docsArticleMax: 860;   // px
}
```

Fonts are self-hosted (the site already self-hosts AHN + JetBrains Mono under `website/public/fonts/`).
Silkscreen is copied from `frontend/src/pipeline/themes/pixel/fonts/` (OFL, with its `OFL.txt`) — a file copy,
not an import, so the site still shares no code with the dashboard.

## Shape

```ts
type Route = '/' | DocsRoute | '/:pathMatch(.*)*'
type DocsRoute = '/getting-started' | '/pipeline' | '/runs' | '/dashboard' | '/guidance'
  | '/architecture' | '/configuration' | '/cli'
  | '/topics' | '/topics/routing' | '/topics/proposals' | '/topics/evolution' | '/topics/memory'

// One registry drives the World map, the page HUD, PREV/NEXT and document.title.
type DocsPage = {
  path: DocsRoute
  group: 'START' | 'WORLD 1 · THE LOOP' | 'REFERENCE'
  code: string          // '0-0' | '1-1'…'1-4' | 'R-1'…'R-4' | 'R-4.1'…'R-4.4'
  hud: string           // e.g. 'WORKBENCH · SPEC'
  title: string         // world-map label and h1
  lead: string
  parent?: DocsRoute    // only the four topic sub-pages: '/topics'
  summary?: string      // the four topic sub-pages: one line for the Topics page's "In this section" list
  view: string          // the views/*.vue component; the router builds every docs route from the registry
}
// levelLabel = (group === 'REFERENCE' ? 'REF ' : 'LEVEL ') + code
// STAGE SELECT = the page's h2 sections in order, numbered 01, 02 …; "STAGES <n> · READY" counts them

type DocBlock =                          // the prototype's CONTENT block kinds, one component each
  | { k: 'p', t: InlineText } | { k: 'h3', t: string } | { k: 'code', t: string }
  | { k: 'note', tone: 'WARN' | 'TIP', t: InlineText }
  | { k: 'shot', src: Asset, alt: string, cap: string }
  | { k: 'list', items: InlineText[], ordered: boolean }
  | { k: 'table', cols: string[], rows: string[][], mono: number[], grid?: string, minW?: string }  // grid/minW: the prototype's per-table column template
type InlineText = string                 // **bold**, `code`, [link](to) — as the prototype's segs
```

World map, in order (prototype + Gap G1):

| Group | Code | Title | Path | HUD |
|---|---|---|---|---|
| START | 0-0 | Getting Started | /getting-started | GETTING STARTED |
| WORLD 1 · THE LOOP | 1-1 | Pipeline | /pipeline | WORKBENCH · SPEC |
| | 1-2 | Runs | /runs | RUNS · RUN |
| | 1-3 | The dashboard | /dashboard | OBSERVABILITY · WATCH |
| | 1-4 | Guidance | /guidance | GUIDANCE · COUNT |
| REFERENCE | R-1 | Architecture | /architecture | ARCHITECTURE |
| | R-2 | Configuration | /configuration | CONFIGURATION |
| | R-3 | CLI Reference | /cli | CLI REFERENCE |
| | R-4 | Topic Wikis | /topics | TOPIC WIKIS |
| | R-4.1 | Routing | /topics/routing | TOPIC ROUTING |
| | R-4.2 | Proposals | /topics/proposals | TOPIC PROPOSALS |
| | R-4.3 | Drift & Evolution | /topics/evolution | DRIFT & EVOLUTION |
| | R-4.4 | Topics × Memory | /topics/memory | TOPICS × MEMORY |

## Screens

### Header (img 1, 6)

| Where | Left | Right |
|---|---|---|
| `/` | logo square + `regin` (Silkscreen 20/700) + `BETA` badge | `Guide` → /getting-started, `GITHUB` key (outlined, hover inverts to ink bg) |
| docs routes, 404 | logo + `regin` + `DOCS` badge; logo links to `/` | `Home` → /, `GITHUB` key |

The pixel footer renders on `/` only (the prototype's docs pages end at PREV/NEXT). Sticky, 56 px high, `rgba(0,0,0,.9)` + blur(8px), 2 px bottom rule `#20221f`. No hamburger: two links fit at 390 px (img 5, 10).

### Landing `/` (img 1–5) — sections top to bottom, ids as anchors

| # | Section (`id`) | Contents, 1:1 |
|---|---|---|
| 1 | Hero (`top`) | HUD bar `WORLD 1 · REGIN BUILDS REGIN` / `FEATURES 109 TASKS 378 DEFECTS 139 SHIPPED 30%`; kicker `▶ AI AGENT WORKBENCH`; h1; lead; `▶ CLONE ON GITHUB` primary key + `Play the loop ↓` (→ `#play`); QUICK START box with COPY key; hero shot `overview` + figcaption; robot scene; loop nav of four tiles `1-1 SPEC Workbench` → `#workbench`, `1-2 RUN Runs` → `#runs`, `1-3 WATCH Observability` → `#observability`, `1-4 COUNT Guidance` → `#guidance` (tile text = `LAYERS` / `PILLARS` in `content/features.js`) |
| 2 | Level 0 (`play`) | HUD `LEVEL 0 · PLAY THE LOOP · FEATURE #139` / `STAGE n/5 TASKS DONE d/7 OPEN DEFECTS k`; h2 + lead; five-key stage track; left text column (kicker, h3, body, `code`, action key); right "screen" (title bar + `REGIN · PIXEL`, offset shadow) — see state machine |
| 3 | Level 1-1 (`workbench`) | HUD with 4 progress pips + `F139 ROUNDS 1 ESCAPES 0 COST $140.43 TIME 7H17M`; h2 "A feature is a row, and done is a claim."; lead; ledger `pre`; caption; `HOW THE PIPELINE WORKS →` /pipeline; shot `taskgraph`, caption `QUEST LOG · F139 SLICES AS A TASK GRAPH` |
| 4 | Level 1-2 (`runs`) | `DONE 1 RUNNING 2 ARMED 2 AGENTS CLAUDE · KIMI`; "Launch the run, then steer it."; `regin task list` `pre` (armed gold, running gold, done green); `HOW RUNS ARE LAUNCHED →` /runs; shot `taskboard` |
| 5 | Level 1-3 (`observability`) | `TRACE 3B3ADBBC · TURN 8`; "A session viewer, not a scrollback."; tiles SPANS 552, TOOLS 16, TOOL-ATTRIBUTED COST $5.59 OF $55.14 with a two-part meter (green share = 5.59/55.14); `HOW CAPTURE WORKS →` /dashboard; shot `trace` |
| 6 | Level 1-4 (`guidance`) | `VIOLATIONS 2 FIXED SAME TURN`; "Rules the agent can't drift past."; PostToolUse rule-check `pre`; `PATTERNS, RULES AND MEMORY →` /guidance; shot `handlers`, caption `46 HOOK HANDLERS · GATE · TRACE · ENRICH` |
| 7 | Continue | green-bordered box, HUD `WORLD 1 CLEAR` / `CONTINUE? ▶ YES`; h2; lead; `▶ CLONE ON GITHUB` + `Read the guide first →`; WHAT REGIN IS NOT / SUPPORTED AGENTS |
| 8 | Footer | `REGIN · RUNS LOCALLY · NO HOSTED SERVICE` / `GITHUB.COM/CAIUSWANG/REGIN` |

Level sections 1-1…1-4 are one component (`LevelSection`): HUD bar + two-column grid
`repeat(auto-fit, minmax(min(100%, 420px), 1fr))`, text left, figure right; the pip row lights 1–4 pips.

### Level 0 — Play the loop (img 2)

Data is the prototype's constants verbatim: `PT` (the F139 title), `P_TASKS` (9), `P_NODES` (7), `P_COLS`,
`P_TIMELINE` (8 ticks), `P_DEFECTS` (3), `P_LEDGER` (8 lines), `P_STAGES` (5: label, kicker, title, screen, cmd, body).

Axis 1 — which stage is shown:

```
stage ∈ {0 FEATURE, 1 TASKS, 2 GRAPH, 3 DEFECTS, 4 SHIP};  reached = max stage ever entered
[*] → 0
s --act, s done, s<4-->  s+1         (reached = max(reached, s+1))
s --click track key i, i ≤ reached, not busy--> i
4 --act, 4 done (PLAY AGAIN)--> 0    (every progress field reset)
track key i > reached: disabled, ink #6d766f, cursor not-allowed
```

Axis 2 — progress inside a stage (drawn separately because it is orthogonal to which stage is shown):

| Stage | Progress state | `act` does | Done when | Key label before → when done |
|---|---|---|---|---|
| 0 FEATURE | `typed` 0…len(PT), `specced` | types PT one char / 70 ms; once typed, sets `specced` (spec block 0.15 → 1 opacity) | `specced` | `+ NEW FEATURE` → `WRITE SPEC` → `NEXT: TASKS ▶` |
| 1 TASKS | `shown` 0…9 | reveals a task / 180 ms (fade + 8 px rise); #356 READY green, rest WAITING | `shown = 9` | `TRANSCRIBE SLICES` → `NEXT: GRAPH ▶` |
| 2 GRAPH | `tick` 0…7 | advances `P_TIMELINE` / 650 ms; node DONE green, RUNNING gold, READY ink, WAITING edge; arrow green once its column is done; note line changes at tick 0 / middle / 7 | `tick = 7` | `▶ RUN READY TASKS` → `NEXT: DEFECTS ▶` |
| 3 DEFECTS | `fixed[3]` | fixes the first open defect; clicking a bug fixes that one (bug squashes to 0.3, edge green, `CLOSED · CHECK NAMED`) | all fixed | `FIX NEXT DEFECT` → `NEXT: SHIP ▶` |
| 4 SHIP | `lines` 0…8 | prints a ledger line / 160 ms; at 8 shows `★ LEVEL CLEAR` / `ROUNDS 1 · ESCAPES 0 · NEVER WALKED BACK` | `lines = 8` | `SHIP #139` → `↺ PLAY AGAIN` |

While a timer runs (`busy`), the key reads `· · ·` and is disabled, and track keys do not navigate.
Key colour: green while the stage is not done, ink (`#f6f8f6`) once it is. HUD: `OPEN DEFECTS` is 0 before
stage 3, then the count of unfixed defects. Track key text: `★ ` once reached/done, then `0-<n>` and the label.
Leaving the page clears the timer.

### Hero scene

The prototype's sprite robot (box-shadow pixel art, 4 px cells) walks across four step pads (16 s loop,
`steps()` timing, two-frame leg cycle), the pad under it lights green, a gold bug at the right edge squashes,
and `+1 DEFECT` pops. `role="img"`, aria-label as in the prototype. Port `rg-walk`, `rg-f1/2`, `rg-bug`,
`rg-squash`, `rg-pop`, `rg-s0…3` keyframes as-is.

### Docs shell (img 6–10)

| Part | Rule |
|---|---|
| Layout | `max-width 1320; padding 24; flex-wrap; gap 32`. Aside `flex: 1 1 220px`, main `flex: 999 1 560px` (the aside wraps above main on narrow screens — img 10) |
| World-map aside | bordered panel `WORLD MAP`; group labels in Silkscreen dim; entries `code title`; current entry: 4 px green left edge, `#10311d` fill, ink 700, code green, `aria-current="page"`. Sticky `top: 80px` at ≥ 880 px wide, static below |
| Page card (`article`) | HUD row `LEVEL 1-1 · WORKBENCH · SPEC` / `STAGES 9 · READY`; h1; lead; rule; `STAGE SELECT` chips `01 The loop` … — each chip scrolls smoothly to its section with a 76 px offset |
| Sections | h2 with a green Silkscreen number (`01`…) on generic pages; Pipeline uses its own drawn headings (`STAGE 1 Spec` …) |
| Blocks | one component per `DocBlock` kind; `shot` = bordered figure + `8px 8px 0 #10311d` + figcaption; `note` = tone chip (`WARN` gold border/box, `TIP` green) |
| Foot | `◀ PREV · <code>` / `NEXT · <code> ▶` cards in world-map order; NEXT card has the green border; first page has no PREV, last no NEXT |

### Docs pages

| Page | Content source |
|---|---|
| /pipeline | Drawn in full (img 6, 7): `LOOP`, `FACTS`, `SLICES`, `SURFACES` tables and the nine sections, verbatim; section ids are the prototype's `s-loop` … `s-where` |
| /getting-started, /runs, /dashboard, /guidance, /architecture, /configuration, /cli, /topics | The prototype's `CONTENT[<id>]` block lists, verbatim (img 8, 9), in `content/pages.js`. The CLI page's command groups keep coming from `content/cli.js` (held to `regin --help` by a test) rendered as a `table` block. /topics ends with an "In this section" list built from the registry's sub-page summaries (G1) |
| /topics/routing, /proposals, /evolution, /memory | Not in the prototype — Gap G1 |
| 404 | Not in the prototype — Gap G2 |

## Gaps — what the prototype does not show, and what to build

| # | Gap | Build |
|---|---|---|
| G1 | The four Topics sub-pages exist today but are not drawn | Same docs shell and block components; their text is today's view text converted to `DocBlock`s. World-map entries `R-4.1…R-4.4` sit indented under `R-4` (12 px further left padding, same styling) and are listed only while the current route is under `/topics`. They join the PREV/NEXT order after `R-4`. `TopicsSubnav` is removed (the world map replaces it) |
| G2 | No 404 | Docs shell without a current world-map entry; card HUD `GAME OVER · 404`, h1 "Page not found", lead naming the path, one primary key `▶ CONTINUE` → `/`. `document.title` = `Page not found · regin` |
| G3 | Prototype routes are hashes (`#/pipeline`) because it is a static file | Keep history routes (`/pipeline`). Prototype links `Regin Docs.dc.html#/x` → `/x`, `Regin Landing.dc.html` → `/`. Router `scrollBehavior` offset 76 px |
| G4 | 390 px: docs tables (loop, slice, "where to find it", Getting Started tables) clip on the right (img 10) | Under 640 px each table row stacks: label cell on its own line, then the other cells; nothing is cut |
| G5 | 390 px: `pre` blocks clip their lines (img 5, 10) | `pre` scrolls horizontally inside its box (`overflow-x: auto`); the page itself never scrolls sideways |
| G6 | 390 px: long HUD bars run past their border (Level 1-3, img 5) | HUD bars wrap onto a second line (flex-wrap, row gap 6 px) |
| G7 | 390 px: Level 0 track labels truncate to `N…`, `TA…` (img 5) | Under 640 px the track keys show the code only (`★ 0-1`); the kicker already names the stage, and each key's `aria-label` keeps `<code> <label>` for assistive tech. At 640 px and wider a label wraps rather than truncating ("NEW FEATURE" is wider than its key up to ~800 px); at 1440 every label fits on one line |
| G8 | Hover/focus are drawn only on some elements | Every link and button gets a visible focus ring: `outline: 2px solid #2bde5e; outline-offset: 2px` (the prototype's own focus style), on `:focus-visible` |
| G9 | Motion | `prefers-reduced-motion: reduce` stops the hero scene (prototype rule) **and** makes Level 0 timers complete instantly (the stage jumps to its done state) and smooth-scroll become instant |
| G10 | COPY key states | Copies the two quick-start lines; label `COPIED ✓` for 2 s; when the Clipboard API is missing or rejects, the label reads `COPY FAILED` for 2 s instead of silently doing nothing |
| G11 | Two assets arrive truncated (`taskboard.png`, `memory.webp`) | Recapture from the live dashboard in Pixel dark with the sidebar collapsed, 1440 × 900 at 2×, same framing as the prototype's visible top strip: the Tasks board view, and `/memory` |
| G12 | Meta | `index.html` title/description keep "workbench" and never "harness"; add `<meta name="theme-color" content="#000">` |

## Rules

- R1 Every visual in the Fidelity table's first three rows matches the prototype at 1440 × 900 (desktop).
- R2 One `DocsPage` registry (the world-map table above) is the only list of docs pages; aside, HUD, PREV/NEXT,
  router titles and the 404's "no current entry" all read it.
- R3 Copy is the prototype's, verbatim, including the dated numbers.
- R4 Dark only: the theme toggle, `data-theme`, `regin-site-theme` storage, `ThemedShot` and every `-light`
  shot are removed; nothing on the site renders a light palette.
- R5 Screenshots live in `website/src/assets/shots/pixel/<name>.webp` (14 delivered + 2 recaptured), each
  ≥ 2560 px wide. Shots no longer imported anywhere are deleted.
- R6 Fonts are self-hosted; the built site makes no request to a third-party origin.
- R7 The landing keeps the four layers in order Workbench, Runs, Observability, Guidance (from `content/features.js`).

## Invariants

- I1 At 390 px and 1440 px, on every route, `document.documentElement.scrollWidth ≤ innerWidth`.
- I2 Exactly one world-map entry has `aria-current="page"` on every docs route; none on `/` and the 404.
- I3 Every route in the Shape's `Route` type resolves to a page that is not the 404 (except the catch-all).
- I4 No inline `style=""` attributes carry design tokens in the shipped Vue (the prototype's inline styles become classes / CSS variables in `site.css`).

## Acceptance

Tests live in `tests/docs/test_website_pixel.py` (Python, source/build checks) and
`website/e2e/*.spec.js` (Playwright, run against `npm run preview`). The website gets its own
`@playwright/test` dev dependency and `playwright.config.js`; it shares nothing with `frontend/`.

```gherkin
Feature: pixel website

  Scenario: tokens match the prototype                      # test_palette_and_fonts
    Given website/src/assets/site.css
    Then it defines the Palette colours, the three font families with @font-face for Silkscreen 400 and 700
    And it contains no colour outside the Palette except rgba() overlays

  Scenario: no third-party requests                          # e2e: no-external-requests.spec.js
    When every route is loaded from the production build
    Then no request leaves localhost

  Scenario: the landing renders the eight sections in order  # e2e: landing.spec.js
    When I open /
    Then the sections are Hero, play, workbench, runs, observability, guidance, Continue, footer
    And the hero HUD reads "FEATURES 109" "TASKS 378" "DEFECTS 139" "SHIPPED 30%"

  Scenario: loop tiles jump to their level                   # e2e: landing.spec.js
    When I click the tile "1-3 WATCH"
    Then #observability is at the top of the viewport below the header

  Scenario: playing the loop end to end                      # e2e: play-the-loop.spec.js
    Given Level 0 at stage 1 with keys 0-2…0-5 disabled
    When I press the action key until it reads "↺ PLAY AGAIN"
    Then I passed NEW FEATURE, TASKS, GRAPH, DEFECTS, SHIP in order
    And the HUD read "TASKS DONE 7/7" after GRAPH and "OPEN DEFECTS 0" after DEFECTS
    And the screen shows "★ LEVEL CLEAR"

  Scenario: clicking a bug fixes that defect                 # e2e: play-the-loop.spec.js
    Given Level 0 at DEFECTS with 3 open
    When I click the second bug
    Then it reads "CLOSED · CHECK NAMED" and the HUD reads "OPEN DEFECTS 2"
    And the first and third still read "OPEN · CLICK THE BUG"

  Scenario: the track cannot skip ahead or interrupt         # e2e: play-the-loop.spec.js
    Given Level 0 at TASKS while tasks are being revealed
    Then the action key reads "· · ·" and is disabled
    And clicking the 0-1 key does not change the stage

  Scenario: play again resets every stage                    # e2e: play-the-loop.spec.js
    Given Level 0 cleared
    When I press "↺ PLAY AGAIN"
    Then the stage is 1/5, keys 0-2…0-5 are disabled and the title field is empty

  Scenario: reduced motion                                   # e2e: reduced-motion.spec.js
    Given prefers-reduced-motion is reduce
    Then the hero robot has no running animation
    And pressing "TRANSCRIBE SLICES" shows all 9 tasks immediately

  Scenario: copy quick start                                 # e2e: landing.spec.js
    When I press COPY
    Then the clipboard holds "git clone https://github.com/caiuswang/regin\ncd regin && ./scripts/setup.sh"
    And the key reads "COPIED ✓"
  Scenario: copy without clipboard permission                # e2e: landing.spec.js
    Given the clipboard rejects writes
    When I press COPY
    Then the key reads "COPY FAILED"

  Scenario Outline: world map marks the current page         # e2e: docs-shell.spec.js
    When I open <path>
    Then the world-map entry "<code>" has aria-current="page" and it is the only one
    And the card HUD reads "<label>" and the h1 reads "<title>"
    Examples:
      | path              | code  | label                         | title           |
      | /getting-started  | 0-0   | LEVEL 0-0 · GETTING STARTED   | Getting Started |
      | /pipeline         | 1-1   | LEVEL 1-1 · WORKBENCH · SPEC  | Pipeline        |
      | /cli              | R-3   | REF R-3 · CLI REFERENCE       | CLI Reference   |
      | /topics/routing   | R-4.1 | REF R-4.1 · TOPIC ROUTING     | Routing         |

  Scenario: topic sub-pages appear only inside Topics        # e2e: docs-shell.spec.js
    When I open /runs
    Then the world map lists 9 entries
    When I open /topics
    Then it lists 13 entries, R-4.1…R-4.4 indented under R-4

  Scenario: STAGE SELECT counts and jumps                    # e2e: docs-shell.spec.js
    When I open /pipeline
    Then the HUD reads "STAGES 9" and there are 9 chips
    When I click "06 Defects"
    Then the Defects section's top is 76 px below the viewport top

  Scenario: PREV and NEXT walk the world map                 # e2e: docs-shell.spec.js
    Then /getting-started has no PREV and NEXT is "1-1 Pipeline"
    And /topics/memory has PREV "R-4.3" and no NEXT
    And /topics NEXT is "R-4.1 Routing"

  Scenario: 404                                              # e2e: docs-shell.spec.js
    When I open /no-such-page
    Then the card HUD reads "GAME OVER · 404", no world-map entry is current
    And "▶ CONTINUE" leads to /

  Scenario Outline: nothing scrolls sideways                 # e2e: responsive.spec.js
    Given a <width> px wide viewport
    When I open each route
    Then scrollWidth ≤ innerWidth
    And every table row and pre fits or scrolls inside its own box
    Examples: | width | 390 | 1440 |

  Scenario: desktop matches the prototype                    # e2e: fidelity.spec.js
    Given the prototype screenshots 1, 3, 4, 6, 8 at 1440 × 900
    When the same viewport renders /, /pipeline, /getting-started
    Then section boundaries (top, height) are within 8 px of the prototype's
    And the h1/h2 font is Atkinson Hyperlegible Next 800 and HUD labels are Silkscreen

  Scenario: dark only                                        # test_dark_only
    Then no website source mentions data-theme, regin-site-theme, ThemedShot or a "-light" shot

  Scenario: shots are the prototype's                        # test_shots_are_pixel_captures
    Then every image the site imports is under assets/shots/pixel/ and ≥ 2560 px wide
    And taskboard and memory decode whole (no truncated file)

  Scenario: build and routes                                 # test_website_builds_and_routes_resolve (kept)
    Then `npm run build` succeeds and every DocsRoute resolves to a non-404 view
```

### Existing F153 tests this changes

| Test (`tests/docs/test_website_introduction.py`) | Change |
|---|---|
| `test_harness_is_no_longer_the_frame` | kept as is |
| `test_layers_keep_their_order_on_the_website` | layer/pillar order kept; the `SiteHeader` `NAV` assertion moves to the `DocsPage` registry order (the header no longer carries the page list) |
| `test_cli_reference_is_complete` | kept |
| `test_new_pages_show_the_real_product` | replaced by `test_shots_are_pixel_captures` (dark-only, R4/R5) |
| `test_website_builds_and_routes_resolve` | kept; `ROUTES` gains `/topics/proposals`, `/topics/evolution`, `/topics/memory` |

## Work slices

Base: `f154/integration` (create it from `master` before the first slice). Each slice merges its branch into
`f154/integration` before it reports finished. S3, S4 and S5 run in parallel after S2; they touch
`site.css` — each appends its own block and resolves the merge, never rewrites another slice's rules.
Every e2e run uses its own preview port so parallel verifies do not collide.

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Assets + tokens | Fetch the 14 whole assets via DesignSync into `assets/shots/pixel/`; recapture `taskboard` + `memory` (G11); copy Silkscreen + OFL; Palette/Fonts/Chrome as CSS variables in `site.css`; `@playwright/test` + `playwright.config.js` + `e2e/` scaffold in `website/`; `tests/docs/test_website_pixel.py` with the token + shot tests | Tokens, R5, R6, G11, G12 | test_palette_and_fonts, test_shots_are_pixel_captures, no-external-requests.spec.js | — | — | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test no-external-requests && cd .. && .venv/bin/python -m pytest -q tests/docs/test_website_pixel.py |
| S2 | Shell + docs registry | Header (two variants), footer, `DocsPage` registry, docs layout (aside + page card + STAGE SELECT + PREV/NEXT), `DocBlock` components, 404 (G2), routes incl. three new topic paths (G3), dark-only removal (R4), focus ring (G8) | Header, Docs shell, Shape, R2, R4, G1 (registry), G2, G3, G8, I2, I3 | docs-shell.spec.js, test_dark_only, test_website_builds_and_routes_resolve | — | S1 | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test docs-shell && cd .. && .venv/bin/python -m pytest -q tests/docs |
| S3 | Landing static | Hero (HUD, copy, CTAs, COPY key G10, hero shot, loop tiles), `LevelSection` ×4, Continue, footer — everything on `/` except Level 0 and the robot | Landing rows 1, 3–8, R3, R7, G10 | landing.spec.js | — | S2 | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test landing |
| S4 | Level 0 + hero scene | `PlayTheLoop` (state machine, five screens, timers, bug sprite) and the robot scene with keyframes; reduced-motion handling (G9) | Level 0, Hero scene, G9 | play-the-loop.spec.js, reduced-motion.spec.js | — | S2 | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test play-the-loop reduced-motion |
| S5 | Docs content | Pipeline page as drawn; every other docs page from `CONTENT` as `DocBlock` lists; topic sub-pages converted (G1); `TopicsSubnav`, `DocPage`, `ThemedShot`, `TourShot`, `HomeProof`, `HomePillars` and orphaned shots deleted; F153 test updates; drop the strict-xfail marker on `test_site_imports_only_pixel_shots` (it turns red the moment no old shot is imported) | Docs pages, G1, R3, R5 | docs-shell.spec.js, test_layers_keep_their_order_on_the_website, test_cli_reference_is_complete, test_site_imports_only_pixel_shots | — | S2 | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test docs-shell && cd .. && .venv/bin/python -m pytest -q tests/docs |
| S6 | Phone + fidelity pass | G4–G7 reflow rules; `responsive.spec.js` at 390/1440 on every route; `fidelity.spec.js` against the prototype's section boundaries | G4, G5, G6, G7, I1, R1 | responsive.spec.js, fidelity.spec.js | — | S3, S4, S5 | `f154/integration` | goal-verified-treenav | cd website && npm run build && npx playwright test responsive fidelity |
| S7 | integrate | Every slice merged back into `f154/integration`, none landed zero commits | Invariants | test_every_slice_merged_back | — | S6 | `f154/integration` | goal-verified-treenav | — |
| S8 | verify | Full website e2e + `tests/docs` + the parallel pytest suite | Acceptance | test_full_suite_passes | — | S7 | `f154/integration` | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile && cd website && npm run build && npx playwright test |




