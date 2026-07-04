# Bridge composer slash-command autocomplete — design

Adds a `/`-triggered autocomplete popup to the `/live` NOW-zone bridge composer
(`LiveComposer.vue`). Typing `/` at the start of the draft opens a floating
"accept list" of the slash commands **and** skills the target session would
actually accept; arrow-keys / Enter / click inserts `/<name> `.

Status: **built & verified** on `feat/live-mobile-card` (gated behind the same
`agent_bridge.enabled` + reachable-pane conditions as the composer itself).

Implementation note (non-obvious): `LiveCommandMenu` is `<Teleport>`ed to
`<body>` and `position: fixed` (placed from a composer-computed `anchorStyle`),
NOT absolutely positioned inside `.live-composer`. The NOW zone clips overflow
(`.live-now { overflow:hidden; z-index:2 }`), so an in-flow upward menu was
scissored off and `.live-tail` ate its clicks — the teleport lifts it above
both.

## Decisions (agreed)

- **Accept-list = slash commands + skills**, from both project `.claude/` and
  user `~/.claude/`.
- **Scope = the target session's project dir + user home.** Resolve the
  session's project from `bridge_panes.cwd` (already recorded by the slice-1
  SessionStart handler); fall back to regin's repo `.claude/` when unknown.
- **Trigger = start-of-message only** — the menu opens only when `/` is the
  first non-whitespace char and the caret is inside that first token. Matches
  Claude Code semantics (slash commands only fire at message start); no
  mid-sentence / URL false-fires.

Rationale for building fresh: research confirmed **no existing endpoint**
enumerates Claude slash commands (nothing reads `.claude/commands/*.md`), and
`GET /api/skills` returns ids only (no descriptions) over regin's own patterns —
not the target session's accept-list.

## Backend

New module `lib/agent_bridge/commands.py`:

```
list_session_commands(trace_id) -> list[dict]
  # dict: { name, description, kind, scope }
  #   kind  ∈ "command" | "skill"
  #   scope ∈ "project"  | "user"
```

Resolution:
1. `store`-read `bridge_panes.cwd` for `trace_id`. Walk up from cwd to the
   nearest ancestor containing a `.claude/` dir → project root. Unknown/absent →
   fall back to `settings` repo root.
2. Scan four sources, parsing YAML frontmatter `description`
   (reuse the `_parse_frontmatter` idiom from `lib/db_rebuild.py`):
   - `<project>/.claude/commands/**/*.md`  → kind=command, scope=project
   - `~/.claude/commands/**/*.md`          → kind=command, scope=user
   - `<project>/.claude/skills/*/SKILL.md` → kind=skill,   scope=project
   - `~/.claude/skills/*/SKILL.md`         → kind=skill,   scope=user
3. Command name = path stem; nested dirs namespaced `dir:stem` (Claude's form).
   Skill name = the skill dir name (or frontmatter `name`).
4. Dedup by name (project shadows user). Sort by kind then name.

Keep each function under the radon grade gate; the blueprint route stays thin.

New route in `web/blueprints/bridge.py`:

```
GET /api/sessions/<trace_id>/bridge-commands   (@require_editor)
  -> { "commands": [ {name, description, kind, scope}, ... ] }
```

Session-scoped URL so cwd resolution is per-target-session. `@require_editor`
matches `bridge-send` (only editors can act on the composer). Failures degrade
to `{ "commands": [] }` — the composer just shows no menu, never errors.

## Frontend

### Composable `frontend/src/composables/useSlashCommands.js`
Holds all popup state so `LiveComposer.vue` stays under the vue-complexity gate.

- `catalog` (cached per sessionId), `open`, `query`, `activeIndex`, `filtered`.
- `ensureLoaded(sessionId)` — fetch `/sessions/<id>/bridge-commands` once.
- `sync(text, caret)` — recompute `open`/`query` from the draft+caret using the
  start-of-message rule; clamp `activeIndex`.
- `filtered` — catalog filtered by `query` (prefix match ranked before
  substring, on name then description).
- `move(delta)` / `close()` / `accept(item?)` → returns `{ text, caret }` with
  the `/query` token replaced by `/<name> `.
- `handleKeydown(e)` → returns `true` when it consumed the key (↑ ↓ Enter Tab
  Esc while open) so the composer can `preventDefault`.

### Component `frontend/src/components/live/LiveCommandMenu.vue`
- Props `items`, `activeIndex`, `query`; emits `select(item)`, `hover(i)`.
- Floating `role="listbox"` anchored **above** the composer (it sits low in the
  NOW zone → opens upward), styled with the existing `ds-menu*` /
  `--z-dropdown` tokens. Row: `/<name>` (mono) · truncated muted description ·
  small kind badge (command/skill). `[data-highlighted]` on active; active row
  scrolled into view; ~8 rows then scroll; "no match" empty state.
- Testids: `live-command-menu`, `live-command-item`.

### Wiring in `LiveComposer.vue`
- Wrap the textarea+send row in a `position: relative` container so the menu
  can `position:absolute; bottom:100%`.
- `@input` / selection-change → `menu.sync(draft, caret)` + existing `autogrow`.
- `@keydown` → `if (menu.handleKeydown(e)) { e.preventDefault(); return }`
  **before** the current Cmd/Ctrl+Enter send. Plain Enter accepts only while the
  menu is open; otherwise unchanged. `accept()` writes `draft.value`, restores
  the caret via `taEl`, re-runs `autogrow`.
- `aria-expanded` / `aria-controls` / `aria-activedescendant` on the textarea.

## Verification
- Backend: unit-test `list_session_commands` against a temp `.claude/` tree
  (project shadows user; nested command namespacing; missing dirs → `[]`).
- Frontend: Playwright on `/live` — type `/`, assert menu + items; filter by
  query; ↓↓ + Enter inserts `/<name> `; Esc closes; Cmd+Enter still sends;
  plain Enter with menu closed still inserts a newline.
- `vue-runner` complexity check on the touched SFCs; radon on the new module.
```
