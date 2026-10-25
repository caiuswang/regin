Feature 75, slice B. The whole spec is `regin feature spec 75` — read the two tables in it first.

## Defect

`.pattern-content` (`frontend/src/assets/style.css`, ~L1877–1904) is the one stylesheet behind
every markdown surface in regin — trace messages, patterns, inbox, memory, the Pipeline's spec
and task bodies. It styles only part of the language, so a spec written in the house notation
loses exactly the constructs that notation is made of. Measured against a 13px prose baseline:

| Construct | Today | Target |
|---|---|---|
| `h4`–`h6` | 13px / weight 400 — byte-identical to a paragraph | heavier or larger than the paragraph beside it |
| `a` | colour inherited, `text-decoration: none` | distinct colour **and** underlined, in both themes |
| `blockquote` | no inset, no rule, same colour | left rule + inset, or a distinct colour |
| task-list `li` | checkbox **and** a disc marker | no list marker on a checkbox item |
| `hr` | `margin: 0` — touches the text | vertical margin above and below |
| inline `code` | 14px inside 13px prose | equals the surrounding paragraph's font-size |

`h1`–`h3`, `p`, `ul`/`ol`, `pre` and `table` are already right — leave them.

## Invariants

- **One renderer, one stylesheet.** The fix lands once in `.pattern-content` and reaches every
  surface. No per-view override, no second class.
- **Tokens, not literal colours.** Every value added is a CSS variable and holds under
  `data-theme="dark"`.
- **No renderer swap, no `breaks: true`, no sanitisation.** All three are declared non-goals.

## Acceptance — write these red first

`frontend/tests/markdown-typography.spec.js`, asserting **computed style**, not class names:

```gherkin
Scenario: a fourth-level heading is a heading
  Then h4 renders heavier or larger than the paragraph beside it

Scenario: a link looks like a link
  Then an anchor's colour differs from the prose colour, and it is underlined
  And it stays distinguishable under data-theme="dark"

Scenario: a blockquote is set apart
  Then a blockquote carries a left rule and inset, or a distinct colour

Scenario: a task list shows checkboxes, not checkboxes-and-bullets
  Then a list item containing a checkbox has no list marker

Scenario: a rule separates
  Then an hr has vertical margin above and below

Scenario: inline code matches its sentence
  Then inline code's font-size equals the surrounding paragraph's
```

## Gates

```bash
cd frontend && ./node_modules/.bin/playwright test tests/markdown-typography.spec.js \
  tests/task-body-markdown.spec.js tests/proposal-review-markdown.spec.js \
  tests/task-comment-markdown.spec.js
node scripts/dom-measure.mjs --route /features/75 --explain '.pattern-content a'
```

Commit on the current branch. Do not ship the feature — that is the operator's act.
