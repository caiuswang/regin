# Prompts Template — browse by category

## Intent
The Prompts page (`/prompt-templates`) currently shows every template in one flat list. There are 27 agent-prompt skeletons plus the fragments, and no way to narrow them to one category. This feature lets an operator pick a category, such as Defect, Task run or Topic proposal, and see only that category's prompts. It applies to both tabs.

Non-goals:
- User-assignable categories. The category comes from where a prompt is registered. It is not an editable field, so there is no new column and no migration.
- Separate grid sections per category. This layout was tried before and rejected because small groups left empty columns (see the comment above `sortedSkeletons` in `PromptSkeletonsPanel.vue`). The single flat grid stays, and a filter is added on top of it.

## Source of truth
The registry already declares a category on every surface (`PromptSurface.area` in `lib/prompts/registry.py`). The frontend does not use it. Instead it re-derives the category from the slug prefix (`areaOf()` in `PromptSkeletonsPanel.vue`), and that derivation files some rows wrongly:

| slug | registry `area` | current `areaOf(slug)` |
|---|---|---|
| `topic-authoring-standards` | `topic-proposal` | `topic-graph` ✗ |
| `topic-group-buckets`, `topic-split-leaf` | `topics` | `topic-graph` (key mismatch) |
| any area added in Python later | its area | `other` ✗ |

## Shape
```ts
// GET /api/prompt-templates[?kind=…] — every row gains one field
type PromptTemplateRow = { /* existing fields */ area: string }
// area = get_surface(slug).area when the slug is a registered surface, else "other"
```
- The backend adds `area` in `_row_to_dict` (`lib/prompt_templates.py`) by looking up `get_surface(row.slug)`.
- The frontend deletes `areaOf()` and reads `row.area`. It keeps a presentation map `{area → label, color}` keyed on the registry keys (`defect`, `grader`, `memory`, `task-run`, `topic-proposal`, `topics`, `other`). An unknown key falls back to a title-cased label and gray.

## UI rules
| Condition | Behaviour |
|---|---|
| Tab has rows in ≥2 areas | Show a chip bar above the grid/table: `All (N)`, then one chip per area that has rows, as `Label (count)`, sorted by label |
| Tab has rows in exactly 1 area | Hide the chip bar |
| An area has 0 rows in this tab | Show no chip for it |
| A chip is selected | Grid/table shows only rows with that `area`. The existing sort (area, label) and the `custom` badge are unchanged |
| `?category=<area>` in URL | Select that chip on load. Selecting a chip writes it to the URL, and `All` removes the param. Mirror `useTabRoute` |
| `?category=` is unknown or has no rows in this tab | Treat it as `All` |
| `?surface=<slug>` deep link, and a non-`All` category would hide it | Switch the category to the surface's area with `router.replace`, so the editor it opens is visible. `All` already shows it, so the category is left alone |
| Switching tabs | Reset the category to `All`, because the tabs have different area sets. `useTabRoute({ resets: ['category'] })` drops the param only when the tab actually changes |

Invariants:
- The chip counts add up to `All (N)`, and N equals the number of rows the API returned for that tab's `kind`.
- The card eyebrow badge and the chip for a row always show the same label and color, because both come from one map.
- A row whose slug is not a registered surface is always shown under `other`, never hidden.

## Acceptance
```gherkin
Scenario: API carries the registry area
  When GET /api/prompt-templates
  Then every row has an "area" key
  And for each row whose slug is a registered surface, area == get_surface(slug).area
  And topic-authoring-standards has area "topic-proposal"
  And a user-created fragment has area "other"

Scenario: skeleton tab chip bar matches the data
  Given the Agent prompts tab is open
  Then one chip is shown per distinct area among the ?kind=skeleton rows, each with that area's row count
  And the chip counts sum to the "All" count

Scenario: filtering
  When I click the "Memory" chip
  Then only cards with area "memory" are rendered, and their count equals the chip's count
  And the URL has ?category=memory
  When I reload
  Then "Memory" is still selected
  When I click "All"
  Then every skeleton card is rendered and ?category is gone

Scenario: bad category param
  Given /prompt-templates?category=nope
  Then "All" is selected and every card is rendered

Scenario: deep link beats category
  Given /prompt-templates?category=memory&surface=grader-process
  Then the "Grader" chip is selected and the grader-process editor is open and in view

Scenario: fragments tab
  Given the Fragments tab is open with rows in ≥2 areas
  Then the chip bar is shown and filters the table rows the same way

Scenario: slug-prefix derivation is gone
  Then `grep -n "areaOf" frontend/src/components/PromptSkeletonsPanel.vue` prints nothing

Scenario: no sideways overflow
  Then responsive.spec.js stays green for /prompt-templates at phone width (the chip bar wraps)
```

## Gates
- `pytest -q -n auto --dist=loadfile tests/prompts/test_prompt_template_area.py` for the API scenario. The file name must not be `test_prompt_templates.py`, because `tests/topics/` already has one, and two test files with the same basename break collection of the whole suite.
- `prompt-categories.spec.js` for the UI scenarios. A chip is addressed by `[data-category=<area>]` inside the `Filter by category` group.
- `cd frontend && npx vite build`.
- `responsive.spec.js`, run on the mobile and tablet projects.

