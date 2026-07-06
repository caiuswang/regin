# Design: mobile live session-tail card (`/live`)

*This is a frozen v7.x design snapshot, predating the server-phase model. The
current source of truth is
[`.regin/topics/wiki/live-session-mobile-card.md`](../.regin/topics/wiki/live-session-mobile-card.md).*

**Status:** v7.2 — BUILT on feat/live-mobile-card (goal-verified-treenav loop: dual
builders → dual adversarial verifiers → fix loop → /code-review high → fix loop;
final gates: vite build clean, live-card 24/24 + responsive sweep green, zero console
errors). Deferred follow-ups: LiveSheet → Reka Dialog primitives
(focus trap/scroll lock); copy-path consolidation into useCopy; bySpanTime extraction;
visibleRows/subagentIds/NOW-scan memoization; 5.5rem + 92/96px constants → tokens;
isStale guard wrapper.
**v7:** visual detail spec (turn eyebrows, 55%-opacity activity dots, 3-level depth
model, quiet furniture, motion inventory) — see "Visual detail spec" below.
**v3 addition:** sticky "now" zone — assistant-activity card driven by PENDING spans.
**v4 addition:** default **signal filter** — system spans hidden. Motivated by rendering
real data: in session `8e964958` the newest window was dominated by
`turn`/`config.change`/`hook.*`/`harness.*` rows, crowding the ~8 rows a phone has.
**v5 refinements (prototype feedback):** (a) NO inline "⋯ N system spans" markers —
they drew the eye 13 times per window; hidden spans simply don't render, and the filter
sheet's "show system spans" toggle (with hidden count) is the single way back;
(b) **human row language** — span-type labels (`tool.Read`, `assistant_response`) are
dropped entirely; the dot color carries category, the row text says what happened.
**v6 refinement (prototype feedback):** **message-first hierarchy** — assistant
responses are the content, tool calls are the texture. Message rows (prompt /
assistant_response) render full-color at 13px with a 4-line clamp, user turns tinted
`--accent-soft`; tool/skill/rule/agent activity demotes to faint 11.5px one-line
micro-rows (smaller dot, no row border); tapping a message opens its full text sheet
(attrs sheet stays for activity rows).
**Session:** 9a3e49cf-ae9f-4e78-af79-5b891004d706 (goal-verified-treenav arm, recall gate PASS 18 spans)
**v2 pivot:** v1 rendered the `send_to_user` agent-message digest; user wants the actual
**session trace spans, terminal-style** — a live tail card that folds the earlier
session away. The digest design is dropped.

## Decision

Phones don't get the dense trace conversation feed. They get a **standalone `/live/:id?`
route** (no id → latest session) rendering **one card = the session's recent spans as a
terminal-style tail**. Everything older than the window is folded into a single
"⋯ N earlier spans" row; tapping it unfolds one page at a time (cursor pagination) back
to session start. Chrome is minimal: follow-tail on by default, filters/search behind
one filter icon (bottom sheet), row-tap → span-detail bottom sheet. No keyboard nav.

Why: the conversation feed's card headers starve to 0px at 375px and fixing ~15 card
components one-by-one regresses (branch `fix/mobile-responsive-layout`, bd85e24d). The
desktop Terminal tab is a 4-column table + 10-chip filter bar — desktop furniture. A
purpose-built tail card fits by design.

## Choices (user-approved)

| Question | Choice |
|---|---|
| Placement | Standalone `/live/:id?` route, Observability nav group (v1, stands) |
| Content | **Recent session spans, terminal-style** (NOT send_to_user messages) |
| Fold model | Card renders newest ~50 spans; earlier session folded to one "⋯ N earlier spans" row; tap = unfold one page in place (`before_id` cursor), repeatable |
| Ordering | Chronological newest-last, auto-pinned to bottom, "↓ N new" chip when scrolled up (v1, stands) |
| Chrome | Minimal: follow-tail default-on; category filters + search in a filter-icon bottom sheet; span detail as bottom sheet; no j/k, no inline chip bar |
| Streaming FX | Entrance fade/slide + blinking caret on newest row while running; `prefers-reduced-motion` disables (v1, stands) |
| Now zone | Sticky assistant-activity card at the card's bottom edge: shows what the agent is doing *right now*, derived from PENDING spans + latest `assistant_response` (v3 addition) |
| Signal filter | Default view shows signal spans only; system spans don't render at all (no inline markers); failures always visible; "show system spans" toggle in the filter sheet (v4, refined v5) |
| Row language | Human phrasing, no span-type jargon: `You — <prompt>`, response text plain (md syntax stripped), `Read router.js`, `Edited style.css`, `$ npx vite build`, `Agent goal-builder started`, `Rule <id> · 2 findings`; dot color carries the category (v5) |
| Visual hierarchy | Message-first (v6): prompt/response rows are primary — 13px full-color, 4-line clamp, user turns tinted; tool/skill/rule/agent rows are faint 11.5px one-line micro-rows; message tap → full-text sheet, activity tap → attrs sheet |

### Signal filter (default visibility)

The card's job is "let me focus on what matters", not "every span". Two tiers:

- **Signal (always rendered):** `prompt`, `assistant_response`, `subagent.start/stop`,
  all `tool.*`, `skill.*`, `file.edit`, `plan.edit`, `memory.recall`, `rule.check`
  with `findings > 0` — plus **any** failed / denied / rejected span
  (`tool.failure*`, `attributes.rejected|denied`): failures never hide.
- **System (hidden by default):** `turn`, `config.change`, `cwd.changed`, `hook.*`,
  `harness.*`, `environment.*`, `compact.*`, `session.*`, `pre_tool.*`,
  `assistant.thinking`, no-findings `rule.check`, and unrecognized span names
  (unknown ⇒ system, so new span types never flood the card).
- **Reachable, not deleted** (collapse-don't-delete lesson): hidden spans render
  nowhere in the default view — no inline markers (prototyped and rejected: 13 marker
  rows per window drew more attention than the noise they replaced). The single way
  back is the filter sheet's "show system spans" toggle (labeled with the hidden
  count), which disables the tier filter entirely; system rows then render in faint
  styling, and category chips cover the full set (including `thinking`).
- **Row language is human** (v5): no span-type labels. `prompt` → **You** + text;
  `assistant_response` → the text, markdown syntax stripped, 2-line clamp;
  `tool.Read/Edit/Write/Grep` → `Read/Edited/Wrote/Searched <file>`; `tool.Bash` →
  `$ <command_preview>` (mono); `subagent.start` → `Agent <type> started`;
  `skill.*` → `Skill <id>`; `rule.check` → `Rule <id> · N findings`; other tools →
  short tool name + detail. The dot color is the category signal; duration shows
  only when ≥ 1s. Full span taxonomy remains in the row's detail sheet and on desktop.
- The fold row's "N earlier spans" keeps counting **all** spans (it mirrors
  `span_count_total`) — the signal filter is a view concern, not a data concern.

## Layout (one `.card`, four zones)

```
┌─────────────────────────────────┐
│ ● running · 12m · "fix layout…" │  header: status dot · elapsed · goal
│                             [⌕] │  (2-line clamp) · filter-sheet trigger
├─────────────────────────────────┤
│      ⋯ 214 earlier spans        │  the FOLD: prior session collapsed to
│      (tap to load earlier)      │  one row; tap loads a page via before_id
├─────────────────────────────────┤
│ ● tool.Read   router.js         │  two-line stacked rows (no table):
│ ● tool.Edit   style.css   0.4s  │  line 1 = dot · label · duration-right
│ ● tool.Bash   npx vite build…   │  line 2 = detail, truncate/wrap-safe
│   3.2s                          │  ↳ indent kept for subagent children
│ ● thinking…                ▍    │  caret on newest while running
├─────────────────────────────────┤
│         ↓ 3 new spans           │  when scrolled up; else auto-tail
├─────────────────────────────────┤
│ NOW  ◌ running tool.Bash · 12s  │  sticky "now" zone: in-flight span
│ "Tests green — wiring the       │  (live elapsed) + latest assistant
│  route next…"          [more ▾] │  text, 2-line clamp, tap → expand
└─────────────────────────────────┘
```

### The "now" zone (assistant activity card)

Sticky at the card's bottom edge; a pure projection of the already-loaded tail, one
state at a time by priority:

1. **`permreq-*` / `permission.request` PENDING** → amber attention state: "⚠ waiting
   for permission: tool.X" — the highest-value thing to see on a phone.
2. **`pending-*` AskUserQuestion (v8)** → amber "? waiting for your answer" with the
   question text and an `options ▾` opener for the read-only Q&A sheet.
3. **`pending-<tool_use_id>` tool span** → spinner + `terminalSpanLabel` + detail +
   live elapsed (client 1s tick off `start_time`, rolled over past a minute:
   `fmtElapsedSeconds` renders "8m09s"/"1h05m", never a raw seconds dump).
4. **`promptlive-` placeholder** → "processing your prompt…".
5. **Session ended** → the final `assistant_response` under a `✓ finished` header.
6. **Alive + bridge-reachable, nothing pending (v5 idle)** → "idle — waiting for your
   prompt": steady green header dot (no pulse), caret suppressed, full-width composer.
7. **Otherwise** → latest `assistant_response` text (attr `text`), plain-text
   projection (markdown syntax stripped), 2-line clamp; tap **opens a bottom sheet**
   with the full `MarkdownContent` render — never expands in place.

PENDING placeholders arrive through the same shallow-map window and are retired by the
serve-time merge once resolved (`lib/trace/pending_spans.py`, `merge.py`) — the zone
updates on the same 4s poll + `retired_span_ids` prune; no extra endpoint, no extra
state machine beyond the priority pick.

### The bridge composer (v5)

When the session's tmux pane is bridge-reachable (`bridge_reachable` rides the shallow
map's summary; the composer never makes its own polling loop), the NOW zone hosts
`LiveComposer`: full-width in **idle** ("starts the next turn"), a compact **steer**
variant below the response / tool / prompt content ("queues into the running turn"),
and none at all in question / permission / finished. Sends go to the web-JWT proxy
`POST /api/sessions/<id>/bridge-send` (`web/blueprints/bridge.py`), which calls the
delivery layer in-process — the bridge bearer token never reaches the browser. A
`{delivered:false}` refusal or HTTP error surfaces the server's `detail` and preserves
the draft; a delivery clears it. The sent prompt is never appended client-side: it
appears only when the poll returns the real `promptlive-`/`prompt` span. Zone height
changes (composer mount, textarea autogrow) re-pin a pinned tail and re-seat the
"N new" chip via a ResizeObserver in the view.

### Visual detail spec (v7 — the polish layer, grounded in ui-ux-regin-surfaces)

Composition and restraint, extending regin's existing language (slate hierarchy,
scarce accent, data-ink maximization) — not new decoration:

- **Turns are chapters.** Message rows drop the dot; a 9.5px uppercase tracking
  eyebrow carries who + when (`YOU · 09:50` in accent / `ASSISTANT · 09:48` in faint
  slate, `tabular-nums`). Body 13px/1.55 ink, 4-line clamp. User turns keep the
  `--accent-soft` full-bleed tint.
- **Color discipline (60-30-10).** Activity dots render at 55% opacity — category
  color becomes texture. Full saturation is reserved for signal: edit dots (orange),
  failure/denied dots (red), the live caret, and the NOW tag (accent; amber in the
  permission state). No other saturated ink in the tail.
- **Depth model — three elevations.** Tail is flat; the header casts a soft shadow
  *only after scrolling* (`.hd.scrolled`, depth cue that history exists above); the
  NOW zone lifts with an up-shadow; sheets float highest (20px top radius, grabber
  handle, `0 -12px 32px` shadow).
- **Quiet furniture.** Fold row: transparent, 11px faint text, hairline only. No
  scrollbar chrome in the tail (`scrollbar-width: none`). No borders between activity
  rows — separation comes from spacing; hairlines only around message blocks.
- **Motion inventory (all ≤300ms, all reduced-motion-gated):** row entrance
  fade/6px-slide · caret blink · status-dot pulse · NOW spinner · sheet slide-up ·
  "N new" chip pop. Nothing scales, nothing bounces.
- Durations only when ≥1s; times 10px mono tabular right-aligned; message tap → full
  text sheet, activity tap → attrs sheet.
- **Copy action (v7.1):** every bottom sheet header carries a Copy button (SVG icon +
  label, → green `✓ Copied` for 1.5s; `navigator.clipboard` with `execCommand`
  fallback). Payload: message sheets copy the raw markdown text; activity sheets copy
  the most useful attr (`command_preview` → `text` → `file_path`) falling back to the
  span's attrs JSON. No per-row copy icons in the tail — copy lives one tap away in
  the sheet, keeping rows quiet.

### Mobile geometry & long-content rules (375×667 = iPhone SE baseline)

Usable height after browser chrome + app top bar ≈ **520px**. Fixed zones are capped so
the tail keeps the majority:

| Zone | Budget | Rule |
|---|---|---|
| Header | ~56px | goal 1-line clamp on phones (tap → expand) |
| Fold row | ~36px | always one line |
| **Tail** | **~340px ≈ 6–8 rows** | the ONLY scrolling zone |
| Now zone | **≤ ~90px HARD CAP** | 2-line clamp + `env(safe-area-inset-bottom)` |

- **The now zone never grows.** An `assistant_response` can be ~8KB of markdown;
  in-place expansion would push the whole tail off a 667px screen. The zone shows a
  plain-text 2-line clamp (`-webkit-line-clamp: 2`); the `[more]` tap opens the full
  response in a bottom sheet (`MarkdownContent`, internally scrollable,
  `max-height: 80dvh`). The card underneath never reflows; tail scroll position kept.
- **`100dvh`, never `100vh`** for full-height/sticky math — the mobile URL bar
  collapse makes `100vh` overflow and slides the sticky zone under browser chrome.
- Long unbroken strings in rows/sheets (file paths, `command_preview`) get
  `overflow-wrap: anywhere` (same fix the responsive branch applied to `.cell-code`).
- Width 375px: rows are the two-line stack (no table); max ONE `shrink-0` chip per
  line (the duration), everything else `min-w-0 truncate`.

- **Reuse, don't rewrite, the row semantics:** `terminalSpanLabel` / `terminalSpanDetail`
  (`frontend/src/utils/traceFormatters.js`) and `SessionTerminalLog.vue`'s
  `categoryOf`/`dotColor`/`FILTERS` maps. Extract the category+color maps into
  `traceFormatters.js` (or a sibling util) so desktop table + mobile card share one
  source (one-map lesson). Desktop `SessionTerminalLog.vue` is NOT redesigned.
- **Header discipline** (the root cause this exists): max ONE `shrink-0` chip per row;
  everything else `min-w-0 truncate`.
- Icons via `ui/Icon.vue` (no emoji); dark mode rides the invertible `--color-*` ramp
  (no literal hex); shared classes global in `style.css`, not scoped blocks.
- Filter bottom sheet: the 10 categories with counts + search input; applied filters
  shown as a small badge on the trigger. Detail bottom sheet: the tapped span's
  attributes (lazy `fetch-content` when attrs are empty, mirroring `onRowClick`).

## Data + lifecycle (all verified against source)

- No id → `GET /api/sessions?limit=1` → `.sessions[0].trace_id`; the header
  session-switcher (`LiveSessionPicker.vue`, shipped) lists recent 20,
  active-first.
- **Window + fold + tail = the existing shallow map pagination**
  (`web/blueprints/trace/sessions.py::_shallow_map_response`, `?shallow=1`).
  **v7.2 correction (found by verification):** `limit` pages **turn anchors**
  (prompt/compact/session spans), NOT individual spans — `limit=50` on a heavy session
  hydrates every span. The window unit is therefore **turns**:
  - initial: `limit=5` (anchors) + child hydration of the loaded turns → newest ~5
    turns; `span_count_total` drives the fold row's "N earlier spans"
    (= `span_count_total − loaded`, still counted in spans);
  - unfold: `before_id=<oldest_loaded_id>` → 5 more turns + children, prepended in
    place, repeatable to session start;
  - live tail: full-window refetch every ~4s while active (NOT `after_id`: an
    `after_id` request returns an empty window + empty `retired_span_ids` until a new
    turn anchor lands, so mid-turn resolved `pending-*` placeholders would never be
    pruned — verified in `lib/trace/trace_service/queries.py`); the refetch also
    fire-and-forget triggers the server's background transcript rescan.
  - Time format: eyebrows use `HH:MM` (per the visual spec); activity-row times keep
    seconds (`HH:MM:SS`) — a live tail polling at 4s makes seconds meaningful there.
- **Prune `retired_span_ids` every poll.** The client span list is append-only; the
  serve-time merge retires rows (dedup/reparent) and the response says which — skip
  pruning and the card shows duplicates (known two-render-paths gotcha).
- Running signal: active = `status='active'` OR (unset AND last_seen < 10 min);
  poll 4s active → 15s unset-and-stale → STOP at `status='ended'` (header flips to
  `✓ finished · <ended_reason>`). Pause on `document.hidden`.

## Acceptance checklist (falsifiable — build must satisfy all)

1. **375px invariant:** passes `responsive.spec.js` no-horizontal-overflow +
   squished-column detectors (iPhone SE project), fixture = a workflow/subagent-heavy
   session (current worst case; long `command_preview` details must wrap, not overflow).
2. **0 / 1 / N states (v7.2, anchor-paged):** 0 spans → header + "no spans yet" (still
   polling if active); a session with ≤5 turns → no fold row rendered; >5 turns (incl.
   the worst-case fixture) → fold row renders with the true span remainder
   (`span_count_total − loaded`, matches the API), each tap loads one more page of
   turns and decrements the count until it disappears at session start.
3. **Live tail:** a span landing mid-view appears ≤5s (after_id poll) without full
   re-render; scrolled-up state shows "N new" chip, viewport does not move; retired
   span ids from the poll response are pruned (no duplicate rows after a merge/reparent).
4. **Poll lifecycle:** `status='ended'` → zero further map requests (Playwright request
   interception); active session polls ~4s.
5. **Filter sheet:** category counts in the sheet equal the loaded rows per category;
   applying a filter + live tail keeps the filter; search matches the same attrs the
   desktop Terminal searches.
6. **Detail sheet:** tapping a shallow row lazy-fetches full attributes and renders them;
   sheet dismisses without scroll-position loss.
7. **One source for span row semantics:** mobile card imports label/detail/category/color
   from the shared util — no duplicated maps (grep-assertable); desktop Terminal
   unchanged and still green.
8. **Now zone states:** with a PENDING tool span in the window the zone shows the
   spinner + label + a ticking elapsed; with a PENDING `permission.request` it shows the
   amber waiting state (and outranks a concurrent running tool); with neither it shows
   the latest `assistant_response` text clamped to 2 lines; on `status='ended'` it
   shows `✓ finished` + final response; a resolved PENDING span clears the zone within
   one poll (retired-prune verified).
8b. **Long-content invariant:** with an 8KB markdown `assistant_response`, the now zone
   height stays ≤ ~90px (measured), `[more]` opens the full render in a bottom sheet
   capped at 80dvh, and dismissing the sheet restores the exact tail scroll position;
   at 667px viewport height the tail always keeps ≥ 50% of usable height.
8c. **Signal filter + row language:** with the worst-case fixture, the default view
   renders no `turn`/`hook.*`/`harness.*`/`config.change` rows and no inline marker
   rows; no visible row text contains a `tool.`/`pre_tool.` prefix or raw span-type
   name (grep the rendered DOM); a `tool.failure`/denied span renders even with the
   filter on; the filter sheet's "show system spans" toggle (labeled with the hidden
   count) restores the full log with system rows in faint styling; hidden spans still
   count in the fold row's total; the "N new" chip counts only spans the user would see.
8d. **Message-first hierarchy:** message rows render at 13px/4-line clamp in full ink;
   activity rows at ≤11.5px muted with a 1-line clamp; a screenshot diff confirms an
   assistant response occupies visibly more space than any adjacent tool row; tapping
   a message opens its full text (not the attribute list); tapping a tool row still
   opens attributes.
9. **Dark mode + gates:** correct under `data-theme="dark"`; `vite build` clean;
   vue-complexity/bundle engines pass (component-split if needed); zero console errors.

## Build-phase scaffold

- Skills first: `vue-complexity`, `frontend-style-convention` (+ `ui-ux-pro-max` surfaces).
- Reference components: `SessionTerminalLog.vue` (row semantics, follow-tail),
  `utils/traceFormatters.js`, `SessionTraceView.vue` live-poll wiring (`reload()`,
  `useTraceData`), `style.css` tokens, existing bottom-sheet/Dialog primitives in
  `components/ui/`.
- Hard gates: suite green + fresh-context `/code-review high`.
- Router: `frontend/src/router.js` routes array; nav in `AppLayout.vue` `navGroups`
  (Observability).
- Playwright auth: token in `localStorage.regin_auth_token`; live DB `db/regin.db`.

## goal-feedback bookkeeping (for the build session's step 6)

- included: e7b0d58f af2af485 704b0a16 f5786adf (+ collapse-don't-delete lesson, session
  e812b595; + trace_dual_render_paths retired-rows gotcha, from auto-memory)
- offered:  e7b0d58f af2af485 704b0a16 f5786adf 3c5a91a9 ea990d10 181c11fe
- dead-end: agent-messages-inbox leaf @ 0 mem (wiki compensated; v1 only)
