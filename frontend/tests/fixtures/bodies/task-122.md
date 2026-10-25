Feature 75, slice A. The whole spec is `regin feature spec 75` — read it first.

## Defect

`/tasks/<id>` · Comments prints its source. `frontend/src/components/tasks/TaskComments.vue`
renders the body as `<span class="tc-body">{{ comment.body }}</span>`: a comment carrying
`##`, `**`, a table or two paragraphs shows the marks literally and collapses to one line.
`comments.body` is an authored document (operator and agent resolution notes both write it),
so by the feature's rule it renders.

## Fix

Render through `MarkdownContent` — the one renderer, class `.pattern-content` — exactly as
the task body already does in `TaskBodyEditor.vue`. Do not add a second renderer, do not set
`breaks: true`.

Two things must survive:

- **Edit shows source.** The composer (`data-testid="task-comment-draft"`) holds the bytes
  typed, unrendered.
- **The row layout.** `.tc-body` is a flex child (`flex: 1; min-width: 0`); a block-level
  render must not break the comment row or its avatar/meta column.

Do NOT touch `defects.symptom` or `defects.missing_check`. Those are quoted speech acts and
stay plain — slice C is the watch that proves it.

## Acceptance — write these red first

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

## Gates

```bash
cd frontend && ./node_modules/.bin/playwright test tests/task-comment-markdown.spec.js \
  tests/task-body-markdown.spec.js tests/agent-prompt-verbatim.spec.js
```

Plus the `vue-complexity` thresholds on the touched SFC (the PostToolUse engine reports them).
Commit on the current branch. Do not ship the feature — that is the operator's act.
