# Session detail view — header

The full state of the trace header (`SessionTraceHeader.vue`) shares a
`flex-wrap` row with the actions column. At the widths where the two compete
the identity side is squeezed to whatever the actions leave (~625px of 1312px
at 1680w), and everything in it wrapped: the status pill right-aligned to the
column edge ~150px clear of the title it labels, the identity run broke
mid-list and stranded a `·` at the break, `started …` landed alone on a line,
and the feature attribution took three. Four peer facts carried four visual
treatments (bordered chip / `<code>` / plain mono / plain text). One extra
`plans 3` chip pushed the whole block down a row, because the *fixed* identity
facts and the *variable-length* pivot chips shared one wrapping run.

## Shape

Full state, top to bottom — one concern per row, each row shrinking or
truncating rather than wrapping:

| Row | Owner | Contents | Length |
|---|---|---|---|
| eyebrow | `SessionTraceHeader` | `‹ Sessions` | fixed |
| title | `SessionTraceHeader` | h1 + title-source badge + status pill | 1–2 lines |
| facts | `SessionHeaderFacts` | project · trace id · model · started | **fixed** (≤4 chips) |
| pivots | `SessionHeaderPivots` | stale-snapshot, launched-from, peaked%, +sub, plan(s), workflow(s), tasks | **variable** (0–7 chips) |
| attribution | `SessionAttribution` | declared features | 0–n |
| lists | `SessionHeaderPivots` | expanded plans / workflow runs / tasks | on demand |

The facts/pivots split is the load-bearing one: a fixed-length row cannot be
moved by a variable-length one.

The collapsed (compact) row is unchanged — status dot, title, project name,
mono digest, switcher, Reload — and so is the fold pin in `SessionTraceView`.

## Rules

- **The h1 is `min-w-0`, never `flex-1`.** Grow makes the title box span the
  whole column whatever the title's length, so the `shrink-0` pill
  right-aligns to an edge the text never reaches. Basis-auto sizes the h1 to
  its content, so the pill hugs a short title and still lands at the end of
  the first line on a long one.
- **One chip geometry: `.hdr-chip` in `style.css`.** Global, not scoped — the
  same chip renders inside sibling components (`TraceProjectChip`) a scoped
  rule cannot reach. Tone variants (`-info`, `-success`, `-agent`,
  `-warning`) change colour only. It is unlayered CSS, so it also wins
  against the `<Button>` primitive's own sizing when a chip is a button; the
  coarse-pointer touch floor is put back explicitly for the interactive ones.
- **No `·` separators between chips.** They were siblings of the items, so a
  wrap stranded a dot at the end of the line above. The shared geometry is
  what says "these are peers".
- **Every variable-length chip part is capped** (`.hdr-chip-clip`,
  `max-width: 14rem`) and the chip is `nowrap`: an uncapped plan filename
  eats the row and pushes every sibling to a second line.
- **The pivot row renders nothing at zero chips** — an empty row is not a thin
  row, it is `mt-1.5` of dead space on every session that ran no plan, no
  workflow and kept no task list.
- **The trace id truncates to its head and copies whole on click.** The
  36-char UUID was the single widest element in the header, ~230px of a string
  nobody reads and everybody copies.
- **Icons come from `ui/Icon.vue`,** not emoji.
- **Extraction, not inlining.** `SessionTraceHeader.vue` sits against a hard
  100-binding `vue.template.binding-count` gate; this design took it 90 → 41
  by moving the two rows and the three expanded lists into children, and that
  headroom is the budget for whatever lands here next.

## Measured

| | before | after |
|---|---|---|
| header height @1680×900 | 193.4px | 115.6px |
| title-to-pill gap @1680 | ~150px | 10.0px |
| identity facts @≥1280 | 2 lines | 1 line (20.6px) |
| `SessionTraceHeader` template bindings | 90 / 100 | 41 / 100 |
| horizontal overflow @430px | clean | clean |

## Not built

- The actions column still takes its intrinsic ~671px and starves the
  identity column at 1280–1700px. Bounding it so its controls wrap into two
  right-aligned rows would give the title several hundred more pixels; that is
  a change to the actions, not to the identity block, and was left alone.
- The identity block and the actions are still one `flex-wrap` row with the
  `basis-96` floor that `session-header-collapse.spec.js` pins. A real grid
  would remove the width competition outright.
