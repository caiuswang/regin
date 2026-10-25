# Redesign menu sidebar behavior

The collapsed desktop rail (`AppSidebar.vue`, 4.75rem) drops every label and
leaves an anonymous icon column. Reading it cost a native `title` tooltip, and
five glyphs were generically wrong in the first place. Two independent fixes:
make the glyphs mean something, and give the rail a way to name the row you are
pointing at.

**Chosen affordance: a per-row preview card, not a whole-rail hover-expand.**
Linear/Notion/Gmail/Jira peek the *entire* sidebar over the content on hover;
that was built and rejected mid-design — it moves far more pixels than the one
question ("what is this icon?") justifies. Discord/VS Code preview only the
hovered row, which is what shipped, as a styled card rather than a native
tooltip.

## Types

```
PreviewCard = {
  group:  string     // the section heading the collapsed rail drops
  label:  string
  icon:   NavIconName
  badge:  number | null
  tone:   'info' | 'warning' | 'danger'
  active: boolean
  anchor: { left: px, top: px }   // viewport coords; left = rail.right + 10
}
```

## State machine — the card

```
none ──hover/focus a row (collapsed only)──▶ shown(row)
shown(row) ──hover/focus another row──▶ shown(row')      (same card, slides)
shown(row) ──pointer over nav padding / inter-row gutter──▶ shown(row)  (unchanged)
shown(row) ──nav scrolls under a still cursor──▶ shown(row)  (re-anchored)
shown(row) ──mouseleave nav | focusout of nav | rail expands──▶ none
```

## Invariants

- The rail's own box never changes on hover — width stays 4.75rem, nothing reflows.
- At most one `.sb-preview` exists at any time.
- The card is teleported to `<body>`: the rail sets `overflow: hidden` and would clip it.
- `left` is measured off the **rail's** right edge, never the row's — the rail pads rows inward.
- Nav items carry no `title` in either state; the accessible name (`aria-label`, badge count folded in) is the assistive-tech path and the card is `aria-hidden`.
- Below 768px the rail is `display: none` and the card is too.
- No timers anywhere: every transition is a DOM event (`mouseover`/`mouseleave`/`focusin`/`focusout`/`scroll`).

## Decision table — what the collapsed rail renders

| Rail state | Row labels | Group heading | Badge | Preview card |
|---|---|---|---|---|
| expanded | text | text | numeric pill | never |
| collapsed, idle | — | hairline rule | dot | — |
| collapsed, row hovered/focused | — | hairline rule | dot | that row only |

## Icons

`repos` was a hamburger, `rules` an align-left, `memory` a bar chart, `patterns`
a cube, `audit` a check-circle. Now git-branch, shield-check, database,
book-open, clipboard-check. Every `navGroups` icon name must resolve to a
non-empty path set in `NAV_ICONS`.

## Structure

The nav list lives in `SidebarNav.vue` (owns the delegated preview handlers —
one `v-on` object on the `<nav>`, not four listeners per row) and the card in
`SidebarPreview.vue`. That split also cleared the two template-complexity gates
`AppSidebar.vue` was already failing before this work.

## Not built

- Whole-rail hover-peek overlay (built, then rejected — see above).
- Preview cards for the non-nav rail controls (search / theme / account); they
  keep their native `title`.
- A badged-nav fixture for the e2e DB, so the "badge count appears in the card"
  case still `test.skip`s locally. The dot-vs-pill cases skip for the same
  pre-existing reason.

## Acceptance

`frontend/tests/sidebar-collapse.spec.js` — 16 cases, including: one card per
hovered row naming label + group; re-target never stacks; card clears the rail's
right edge and is parented to `<body>`; survives the inter-row gutter; re-anchors
on nav scroll; keyboard focus previews; expanded rail previews nothing and no
`title` leaks; every glyph draws.
