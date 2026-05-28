# Frontend UI/UX Style Convention

Source: adapted from `docs/frontend-style-convention-proposal.md`
Scope: regin web frontend

## Product Direction

regin is an operator-facing developer tool. The UI should feel calm, neutral,
technical, and legible. It should not feel glossy, playful, brand-heavy, or
marketing-led.

## Core Rules

1. Prioritize operational clarity over novelty.
2. Use neutral surfaces by default.
3. Use saturated color mainly for semantic status and primary action.
4. Keep interaction states visible through structure before color.
5. Do not use underline for selected workflow rows.
6. Do not use high-contrast inversion for neutral active states.

## Typography

- Use the app's existing neutral type direction.
- Heading weights should usually be `600` or `700`.
- Avoid extra-bold hero styling on normal product pages.
- Page titles should usually stay in the `text-2xl` range.
- Body copy should be comfortable and readable, typically `text-sm` to `0.95rem`.
- Description copy should use line-height around `1.6` to `1.8`.

Spacing:

- Make title-to-description spacing explicit.
- Never let the first description line feel attached to the heading.
- Use spacing rhythm, not heavier font weight, to solve density problems.

## Color

Base:

- background: light neutral
- cards/surfaces: white
- borders: soft gray
- primary text: near-black
- secondary text: medium gray
- meta text: muted gray

Semantic accents:

- blue: primary/action/informational
- green: success
- yellow: pending/caution
- red: destructive/error/broken
- purple: optional secondary categorization only when semantically useful

Avoid:

- pink or decorative accent colors unless the system explicitly adopts them
- color used purely for decoration
- black-filled active states for neutral controls

## Interaction

Clickable elements must have:

- pointer cursor
- visible hover state
- visible selected/active state when relevant
- visible focus state

Do not rely on:

- underline alone
- color alone
- invisible row click behavior

For table-driven workflows:

- primary cell should expose a clear button or link when selection matters
- row selection should be indicated with row background and/or border
- selected state must remain calm and structural

Hover:

- use subtle background tint, border emphasis, or text darkening
- never use large brightness jumps
- never switch from white to black or similarly dramatic inversion

Selected state:

- more stable than hover
- neutral background tint + stronger border is preferred
- avoid high-contrast fills for neutral navigation and workflow selection

## Layout

Preferred page structure:

1. page title and context
2. optional summary strip
3. main content

Card rules:

- title
- optional supporting sentence
- clear grouping of actions vs content

Workflow detail headers:

- copy block first
- status/actions second
- stack vertically if horizontal layout compresses the copy

Tables:

- subtle header background
- right-align numeric data
- selected rows need explicit styling
- avoid “maybe clickable” row treatment

## Component Guidance

Navigation:

- top nav stays simple
- active state uses calm tint rather than inversion

Badges:

- semantic only
- not decorative filler

Buttons:

- one primary action per area unless there is a strong reason otherwise
- secondary is neutral border
- danger is only for destructive actions

Forms:

- consistent border/radius/focus ring
- visible labels
- concise help text

## Codex Review Notes

Current frontend inconsistencies to correct over time:

- mixed visual density models across pages
- inconsistent heading spacing
- mixed row-click affordances
- shared CSS and utility classes competing without a clear boundary
- inconsistent active-state language
- semantic color rules mostly implicit, not formalized
- workflow headers easy to make too dense when actions squeeze copy

## Enforcement Rules

When reviewing or implementing frontend work, reject or revise:

- underline-based selection states in workflow tables
- white-to-black or similarly high-contrast active-state jumps for neutral controls
- narrow detail-pane headers caused by forcing actions into the same row as long copy
- decorative accent colors that do not map to semantic meaning
