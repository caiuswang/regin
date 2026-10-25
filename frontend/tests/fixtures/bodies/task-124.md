Feature 75, slice C. Depends on A and B being in the tree. Spec: `regin feature spec 75`.

## What this proves

Two Pipeline fields are declared to **stay plain**, and that is a rule, not an oversight:

| Field | Why plain |
|---|---|
| `defects.symptom` | a declared speech act, quoted verbatim in whoever's words opened it |
| `defects.missing_check` | it names a test, not prose |

A future pass must not "fix" them, and the widened stylesheet from slice B must not have
changed what renders and what does not anywhere else. This slice is that watch.

## Acceptance

`frontend/tests/defect-symptom-verbatim.spec.js`

```gherkin
Scenario: a symptom is quoted, never rendered
  Given a defect whose symptom contains "**not bold**" and a path in backticks
  When the operator opens /defects
  Then the row prints those characters literally
  And its line breaks survive

Scenario: the permalink quotes it too
  When the operator opens /defects/<id>
  Then the symptom and the missing check both print their marks literally
```

## The regression watch — must be green on the tree that has A and B in it

```bash
cd frontend && ./node_modules/.bin/playwright test tests/defect-symptom-verbatim.spec.js \
  tests/task-body-markdown.spec.js tests/agent-prompt-verbatim.spec.js \
  tests/proposal-review-markdown.spec.js tests/task-comment-markdown.spec.js \
  tests/markdown-typography.spec.js tests/defects-ledger.spec.js
```

Anything red here that A or B caused is a defect found **before** the done-claim — fix it in
place, it costs no round. Commit on the current branch.
