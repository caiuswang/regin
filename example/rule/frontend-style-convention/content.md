# frontend-style-convention

Use this skill when working on regin frontend UI/UX so new work stays aligned
with the agreed visual system instead of inventing page-local styling.

## When To Use

Use this skill for:

- redesigning frontend pages or workflows
- refining interaction states, spacing, or information hierarchy
- reviewing frontend consistency
- extracting shared frontend primitives
- deciding whether a UI treatment matches regin's product style

## Working Rules

1. Treat regin as an operator-facing developer tool, not a marketing site.
2. Prefer calm, neutral, high-legibility interfaces.
3. Use border/background/spacing before saturated color.
4. Do not use underline as a row-selection or workflow-selected-state signal.
5. Do not use high-contrast inversion like white-to-black for neutral active states.
6. When a pattern should be reused, prefer shared primitives over page-local one-offs.

## Process

1. Read `references/style-convention.md` before making meaningful UI changes.
2. Compare the target page against the convention's rules for typography, color, interaction, and layout.
3. Reuse existing frontend primitives first: cards, badges, tables, buttons, page headers.
4. If the page needs a new pattern, decide whether it belongs in shared styles or only on that page.
5. After changes, review the result for drift:
   - too much visual emphasis
   - ambiguous click affordances
   - cramped headers
   - inconsistent active states
   - ad hoc accent colors

## Specific Regin Constraints

- Keep visual density efficient, but never cramped.
- Workflow pages may use a stronger summary shell than CRUD/list pages, but they must remain neutral.
- Table + detail-pane workflows must make selection explicit.
- Titles and descriptions need explicit vertical spacing; don't rely on font weight to separate them.

## Auto-Enforced Rules

This pattern ships a self-describing rule bundle (`regin-bundle.yaml`). Once it
is imported and registered as a custom `kind: bundle` rule engine (see this
example's `README.md`), regin's `BundleEngine` runs these Node checkers on every
Vue / CSS / JS / TS edit via the PostToolUse hook:

- `icon_button_requires_label` — icon-only buttons need an accessible name (error)
- `clickable_card_needs_affordance` — clickable surfaces need pointer + hover/focus affordance
- `avoid_raw_hex_in_templates` — raw hex colors flagged when design tokens exist
- `heading_hierarchy_skips` — heading-level skips flagged
- `focus_visible_styling_coverage` — interactive elements need focus-visible styling
- `avoid_native_alert_dialogs` / `avoid_native_confirm_dialogs` — use unified feedback components
- `select_input_in_flex_wrap_row` — inline `.input` in a flex-wrap row needs a width override

The checkers need their npm dependencies installed once (`npm install` inside the
imported bundle dir). Disable a specific rule via `engine_rule_disable` if it
conflicts with a deliberate page-level choice.

## References

- Full convention and review notes: `references/style-convention.md`
