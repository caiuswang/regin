# Pipeline prose renders as the markdown it is

Notation: `docs/spec-notation.md`.

## Intent

Every long-form field on the Pipeline is markdown that somebody — an operator or
an agent — authored: a feature's spec, a task's body, a task comment. Two of the
three already render; one prints its source. And the renderer the other two share
styles only part of the language, so a spec written in the house notation loses
exactly the constructs that notation is made of.

The ask was "调研所有 Pipeline 页面，是否有 Markdown 没渲染或渲染有问题的，并修复".
The survey is done and below. This spec is the fix list it produced.

## Non-goals

- Not a new renderer, and not a markdown library swap. One component renders every
  markdown surface in regin; this widens what it styles, it does not replace it.
- Not sanitisation. The renderer trusts its input today; that is a separate
  decision with its own blast radius (trace, messages, patterns all feed it).
- Not `breaks: true`. CommonMark paragraph semantics stay: regin's own bodies
  separate blocks with blank lines, and flipping the flag would re-wrap every
  trace message too.
- Not `/plans`. It is a different sidebar entry with its own feature.

## The survey — every Pipeline field that carries prose

| Surface | Field | Authored by | Today | Rule |
|---|---|---|---|---|
| `/features/<id>` · Spec card | `features.body` | operator, agent (CLI `feature spec`) | rendered markdown | rendered — it is a document |
| `/tasks/<id>` · body | `tasks.body` | operator, agent | rendered markdown, `image:N` refs resolved | rendered — it is a document |
| `/tasks/<id>` · Comments | `comments.body` | operator, agent (resolution notes) | **source printed inline**, newlines collapsed | rendered — it is a document |
| `/defects`, `/defects/<id>` · symptom | `defects.symptom` | whoever opened it | plain text, `pre-wrap` | **stays plain** — a declared speech act, quoted verbatim |
| `/defects/*` · missing check | `defects.missing_check` | closer | plain text | **stays plain** — it names a test, not prose |
| `/tasks`, `/features` lists | titles only | — | plain text | stays plain — a title is one line |

The two "stays plain" rows are a rule, not an oversight, and are the same pairing
`agent-prompt-verbatim` already makes against the task body: a field that is
*quoted input* keeps its marks; a field that is *an authored document* renders.
A future pass must not "fix" them.

## What the shared renderer drops

Measured on a real page (computed styles, `/tasks/<id>` body, probe body covering
every construct), against a 13px prose baseline:

| Construct | Measured today | Verdict |
|---|---|---|
| `h1`/`h2`/`h3` | 24 / 20 / 18px, weight 600–700 | ok |
| `h4`–`h6` | 13px, weight 400 — byte-identical to a paragraph | **broken**: a fourth-level heading is invisible as a heading |
| link `<a>` | colour inherited from prose, `text-decoration: none` | **broken**: a link is unreadable as a link, and unhoverable-looking |
| `blockquote` | `margin-left: 0`, `padding-left: 0`, `border-left: 0`, same colour, upright | **broken**: a quote is indistinguishable from a paragraph |
| task list `- [ ]` | checkbox renders, `li` keeps `list-style-type: disc` | **broken**: every checkbox carries a redundant bullet |
| `hr` | 1px rule, `margin: 0` | **broken**: the rule touches the text above and below |
| inline `code` | 14px inside 13px prose | **broken**: code sits a step larger than the sentence around it |
| `pre`, `table`, `ul`/`ol`, `p` | styled, table scrolls, code wraps on touch | ok |

Every one of these appears in regin's own feature bodies today — the house spec
notation is headings, tables, links and blockquotes.

## Invariants

- **One renderer.** Every markdown surface in regin styles through the same class;
  a fix lands once and reaches the trace, the messages, the patterns and the
  Pipeline together.
- **Every construct the parser emits has a style.** If the parser can produce the
  element, the stylesheet names it. The gap list above is the current violation.
- **Rendered means the marks are consumed.** No surface shows both the mark and
  its effect.
- **Quoted fields keep their bytes.** `defects.symptom` and `missing_check` render
  as text, always.
- **Edit shows source.** Every editable markdown field edits its bytes in a
  textarea and renders only when not editing. Already true; must stay true.
- **Both themes.** Every style added here is a token, not a literal colour, and
  holds under `data-theme="dark"`.

## Acceptance

Each item is a test, written and seen red before the fix.

`frontend/tests/task-comment-markdown.spec.js`

```gherkin
Scenario: a task comment renders as markdown
  Given a task whose comment body is "## Resolution\n\n**产物**: a branch\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
  When the operator opens that task
  Then the comment shows a heading element, a bold element and a table
  And no literal "##" or "**" appears in it

Scenario: a comment's block structure survives
  Given a comment body of two paragraphs separated by a blank line
  When the operator opens that task
  Then the comment renders two block elements, not one run-together line

Scenario: the composer still edits source
  Given a comment being written
  Then the draft field holds the bytes typed, unrendered
```

`frontend/tests/markdown-typography.spec.js` (asserts computed style, not class names)

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

`frontend/tests/defect-symptom-verbatim.spec.js`

```gherkin
Scenario: a symptom is quoted, never rendered
  Given a defect whose symptom contains "**not bold**" and a path in backticks
  When the operator opens /defects
  Then the row prints those characters literally
  And its line breaks survive
```

Plus the existing `task-body-markdown.spec.js`, `agent-prompt-verbatim.spec.js`
and `proposal-review-markdown.spec.js` stay green: they are the regression watch
that the widened stylesheet changed nothing about what renders and what does not.
