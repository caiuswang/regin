Fix why we could scroll down to the bottom of the page to see a blank area:

![](image:1)

## Symptom

On `/features/:id` (and latently on any long page) the **browser window**
itself scrolled, past the bottom of the app shell, onto a blank slab of body
canvas. The page's own last rows went with it — at 768×700 the final child of
`.content-scroll` measured `bottom: -155px`, i.e. clipped out of reach.

## Root cause

`AppLayout` sizes the shell to the viewport and clips it:

```
.content { height: calc(100vh - 2rem); overflow: hidden; }
.content-scroll { overflow-y: auto; }   /* the one scroller a page gets */
```

`overflow: hidden` only clips descendants for which the clipping box is in the
**containing-block chain**. `.content` was `position: static`, so an
absolutely positioned descendant with no positioned ancestor resolved against
the *initial* containing block and escaped the clip entirely — then
contributed its document-space height to `documentElement.scrollHeight`.

Tailwind's `.sr-only` is exactly such an element (`position: absolute`, all
insets `auto`). On `/features/89` the visually-hidden file input in
`components/tasks/TaskImageStrip.vue` sits ~1368px down the inner scroller:

| viewport | `documentElement.scrollHeight` | `clientHeight` | window scroll |
|---|---|---|---|
| 1280×700 (before) | 1369 | 700 | **669px of blank** |
| 1280×700 (after)  | 700  | 700 | 0 |

A sweep found `.sr-only` escapees on `/defects` too; they simply sat above the
fold, so the same bug was latent there rather than fixed.

## Fix

One declaration — `position: relative` on `.content`. The clip and the
containing block now belong to the same box, so **every** absolutely
positioned descendant of the shell is contained and clipped by it. Auto-
positioned elements (`.sr-only`) keep their static position, so nothing moves;
`relative` at `z-index: auto` creates no stacking context and no containing
block for `position: fixed`, so overlays, popovers, drawers and toasts are
untouched.

Second half (defect 178): `overflow: hidden` still leaves `.content` a
*scroll container* — programmatically scrollable, just without a scrollbar.
`scrollIntoView({block:'start'})` scrolls every scroll-container ancestor, so
revealing `#feature-sessions` near the page foot (more than `.content-scroll`
could travel) scrolled `.content` itself 95–420px and shoved the shell up off
a blank slab with no way back. `.content` is therefore `overflow: clip`: same
clip, same containing block (with `position: relative`), but not scrollable
by anything.

Deliberately **not** done: no per-component patch of `TaskImageStrip` — the
defect is the shell's, and a local `position: relative` would have left every
other `.sr-only` in the SPA free to do it again.

## Invariants

- INV-1 The window never scrolls: `documentElement.scrollHeight ==
  documentElement.clientHeight` on every route, at every width.
- INV-2 `.content-scroll` remains the only vertical scroller a page gets, and
  its last child is reachable at the bottom of the pane.
- INV-3 No descendant of `.content` can grow the document, whatever its
  `position`.
- INV-4 `main.content.scrollTop == 0` always: no `scrollIntoView`, focus or
  hash reveal can scroll the shell, only `.content-scroll`.

## Acceptance

```gherkin
Scenario: a feature page with an attached spec image
  Given /features/89 at 1280x700
  When the page has settled
  Then documentElement.scrollHeight equals clientHeight
  And scrolling the window moves nothing

Scenario: the shell contains what it clips
  Given any route with the app shell mounted
  When an element with class "sr-only" is appended 2400px down .content-scroll
  Then documentElement.scrollHeight still equals clientHeight
  And .content-scroll still scrolls internally

Scenario: the page's own tail stays reachable
  Given /features/89 at 768x700
  When .content-scroll is scrolled to its end
  Then the last child of .content-scroll has a bottom inside the pane
```

Gated by two specs, both of which fail on a pre-fix build and pass after:

| Spec | Guards | Pre-fix failure |
|---|---|---|
| `responsive.spec.js` → `No window scroll — the shell clips its own content` | 6 routes (incl. `/trace/sessions/:id`, the only `fixedViewport: 'lg'` consumer) × the chromium/mobile/tablet projects, plus a synthetic `.sr-only` appended 2400px down the scroller | document grew 2179px |
| `feature-images.spec.js` → `the image strip does not make the window scroll past the shell` | the reported surface itself, stubbed so an empty DB cannot green it | document grew 3986px |

