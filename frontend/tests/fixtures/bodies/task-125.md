Feature 75, slice D. Depends on C. Spec: `regin feature spec 75`.

## Why a whole-tree pass

Slice B edits `.pattern-content`, which is the stylesheet **every** markdown surface in regin
renders through — the trace conversation, agent messages, patterns, the inbox, memory
detail, topic proposals and the Pipeline. A change that is correct on one page can be wrong
three views away, and only a sweep finds that.

## The bars

```bash
.venv/bin/python -m pytest -q -n auto --dist=loadfile
cd frontend && ./node_modules/.bin/playwright test
```

## Browser verification — both themes

Render each surface and look, light **and** `data-theme="dark"` (localStorage token bypasses
the auth guard for Playwright):

| Route | What to check |
|---|---|
| `/features/75` | the spec card — h4s, links, tables, blockquotes of the house notation |
| `/tasks/<a task with comments>` | body and comments render; the composer still shows source |
| `/defects` and `/defects/<id>` | symptom and missing check still literal |
| a trace session's Messages tab | agent messages unchanged |
| `/memory/<id>` | memory detail unchanged |

Use `node scripts/dom-measure.mjs --route <path> --overflow` for anything that looks clipped
or side-scrolling.

## Reporting

Anything wrong is declared, not narrated:

```bash
regin defects open "<what went wrong, in your words>" --feature 75 --session "$(regin session-id)" --path <suspect file>
```

Do **not** run `regin feature ship 75`. Shipping is the operator's act on the feature.
