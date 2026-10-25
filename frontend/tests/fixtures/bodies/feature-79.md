# Redesign Search Palette

The global search palette (`⌘K`) reads as one instrument: a fixed rail of
controls, a result list whose rows show the line that actually matched, and a
preview that opens **at** the hit and lets the reader walk the rest without
leaving the palette.

Origin: https://claude.ai/design/p/6943f377-f05c-42c3-8364-ddfd18e60edd
(`Search Palette - Redesign.dc.html`) — an aesthetic reference, not a contract.
The shipped design diverges where regin's data (spans, diffs, wiki markdown)
demanded it.

Non-goals: the search *backend* (`lib/fulltext/`, scope/corpus routing, ranking)
is unchanged; this is the palette's presentation layer only. No user preference
was added — every branch below is decided by the document, not by a setting.

## Shape

```
SearchDoc   = { id, kind, title, sub, body, tag, mono: bool, local: bool, span? }
ResultRow   = { doc: SearchDoc, hits: int }            # hits = occurrences
HitLine     = { text, line }                           # capped at HIT_LINE_CAP = 200
PreviewMode = 'read' | 'hits'                          # ⌥L flips
Renderer    = 'span' | 'file' | 'line' | 'prose' | 'none'
```

Components: `FullTextSearch.vue` (shell, keyboard, geometry) ▸
`SearchFacetRail.vue` · `SearchResultList.vue` ·
`SearchPreview.vue` ▸ `SearchSpanPreview.vue` | `SearchFilePreview.vue` |
`SearchProsePreview.vue` | `SearchLinePreview.vue`, with `SearchHitList.vue`
as the `hits` mode, `SearchRowTag.vue` naming the kind on both a row and the
preview header, and `SearchMark.vue` marking every one of them. The `#`
lookup / roster mode swaps the panes for `SessionSwitcherList.vue` +
`SessionSwitcherPreview.vue` in the same shell. Pure helpers in
`utils/searchDocs.js`.

## Chrome — one rail, not four bands

Scope, kind facets, recents and the key legend live in a **178px
(`flex: 0 0 11.125rem`) rail inside the panes**, not in horizontal strips above
them. The kind list therefore scales vertically and a scope offering four kinds
cannot run off the right edge.

| Viewport | Rail | Panes |
|---|---|---|
| > 900px | vertical column, left of the panes | results ‖ preview side by side |
| ≤ 900px | lies down as a horizontal scrollable strip; `rail-label`/`rail-foot` hidden | stacked; preview is a push-over sheet (`.is-sheet`), results hidden behind it |

Invariant: the rail is never the thing that disappears at a breakpoint — the
preview is.

## Rows

| Rule | Behaviour |
|---|---|
| Snippet source | the first **matching** line that is not a markdown heading (`/^\s*#{1,6}\s/`), taken from the first 12 hit lines; falls back to `lines[0]`, then to `doc.sub` when the only match is in the title |
| Snippet framing | `snippet()` centres the window on the term (`SNIPPET_BEFORE`/`AFTER`), not on the line start |
| File titles | split on the last `/`: the directory shrinks under pressure, the filename does not |
| Hit count | a weight bar (`width = hits / peak`, `peak` = the busiest row currently listed) **plus** a plain number — a relative signal and an absolute one, not a filled pill that read as both |

## Preview

The renderer is chosen by the document, never by a preference. Kind decides
first; `doc.mono` decides the rest:

| Condition | Renderer | Why |
|---|---|---|
| `kind === 'span'` | `SearchSpanPreview` | a span is replayed through the trace's own renderers, so a hit reads as the turn the reader would see |
| `kind === 'file'` | `SearchFilePreview` | the body is a diff; `DiffBlock` owns it whole |
| `doc.mono` | `SearchLinePreview` (verbatim lines) | a shell log or a file body must not reflow |
| otherwise | `SearchProsePreview` | the body *is* markdown; headings resolve (each carrying its own hit-line count), `code` and **bold** resolve, hard-wrapped paragraphs reflow |

`mono` is set server-side and client-side and is true for more than the mono
span categories (`shell`, `edit`, `tool` in `utils/searchDocs.js`) — session
docs (`lib/fulltext/session_docs.py`) and file docs
(`lib/fulltext/file_docs.py`) carry it too.

`previewWindow()` opens the pane **at the hit** — the window starts 6 lines
above the target when the target is past line 12 — and returns `cursor`, the
hit's index inside the window, so the renderer can mark and scroll to that one
line. The stepper moves that window, so it exists only where there *is* one:
`steppable = hits > 1 && renderer ∈ {line, prose}`. Span and file previews are
inert under ⌥↑/⌥↓ and reach their hits through the ⌥L list instead. A document
shorter than the parked index emits a corrective `step` rather than pointing
past the end.

### Hit walking

| Key | Effect |
|---|---|
| `↑` / `↓`, `⌃p` / `⌃n` | move **between** results |
| `⌥↑` / `⌥↓` | step **within** the highlighted result's hits; position reads `i / n` |
| `⌥L` | flip the body to a concordance of every matching line with its line number (up to `HIT_LINE_CAP`, which the list says when it truncates); picking a line reads there |
| `Tab` / `⇧Tab` | cycle kind chips within the scope |
| `⌃s` | toggle scope · `⌃i` capture idea · `1`–`9` attach by number (roster only) |
| `Esc` | `closeTopLayer()` — peels the narrow-viewport preview sheet first, then closes the palette; stops propagation so the presentation dialog beneath survives |

⌥ composes a character on macOS, so every ⌥ chord must `preventDefault` or `⌥L`
drops `¬` into the query. `onKeydown` delegates to `chordFor()` and
`isRosterDigit()` to stay under the cyclomatic gate.

## Counts

`N matches` (occurrences — what the row bar ranks on) and `N lines` (places to
look) are two quantities, printed as two numbers under two names:
`"12 matches · 5 lines"`.

## Marks

Hit tokens are a term-level **blue tint + underline**, with a ring on the one
hit the stepper is parked on. Amber is regin's semantic warning colour on turn
chrome; a search hit is not a warning.

## Type

Six steps, declared as custom properties on the panel; the palette had eleven
sizes and now draws from these (one literal `0.75rem` survives in
`FullTextSearch.vue`, numerically the meta step):

```
--sp-t-display 1.125rem  preview title, one per pane
--sp-t-input   1rem      the query, the only other large size
--sp-t-body    0.8125rem prose, result titles, hit lines
--sp-t-meta    0.75rem   controls: facets, scope, buttons
--sp-t-micro   0.6875rem counts, crumbs, kickers, legends
--sp-t-kbd     0.625rem  key caps only
```

Monospace takes `0.93em` on the step it inherits rather than opening a second
ladder.

## Acceptance (frontend/tests/search-palette.spec.js)

- `⌥L lists every matching line, and picking one reads there`
- `the preview counts matches and lines separately`
- `patterns render their markdown rather than printing it`
- `sessions keep the plain line preview`
- `a file hit renders through the diff block`
- `facet counts sum to the All count`; `tab cycles the kind chips within the scope`
- `ctrl+s toggles the scope from the keyboard`

Retired by this design: `patterns keep the plain line preview` — patterns
deliberately no longer use the verbatim renderer.
