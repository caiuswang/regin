# Research: what `agent_tasks.body` holds once TipTap edits it

*Compiled 2026-09-14 on branch `research/task-body-editor`, off `feature/cai-274-native-tracker` at `bbf5b360`. Every round-trip number below was produced by running the candidate libraries over the real bodies in this install's `db/regin.db` — not read out of documentation. Version numbers and sizes are dated; the constraints they are measured against are durable.*

## Question

If TipTap/ProseMirror edits a task's `body`, **what does that column then hold** — and what does it cost every other reader of it?

The field is not a UI string. Agents write it, agents read it (`lib/tasks/runs.py:_prompt`, reached through `prompt_for`, turns it into the launch prompt), the CLI prints it, the capture surface bounds it at 8,000 characters and the authenticated edit path at 64,000. A WYSIWYG editor that normalises on every save rewrites text no human touched.

## What the column holds today

| Fact | Where it comes from |
|---|---|
| `body` is a nullable SQLite `TEXT` column with no format declared anywhere. | `lib/orm/models/tasks.py` — `body: Optional[str] = Field(default=None, sa_column=Column("body", Text))` |
| The authenticated edit path bounds it at **64,000 characters and refuses rather than truncating**. The bound was raised from 8,000 *specifically because a feature's whole spec now lives in this column*; the comment records the largest real artifact measured at **29,488 characters**. | `lib/orm/models/tasks.py` (`BODY_MAX = 64_000`), enforced in `lib/tasks/capture.py` `_string` |
| The capture surface keeps the old **8,000** bound, deliberately — it is third-party intake reachable by a machine key. Also refuses rather than truncating. | `lib/tasks/capture.py` (`CAPTURE_BODY_MAX`) |
| Every save already **strips leading/trailing whitespace**. | `lib/tasks/capture.py` `_string` — `value = value.strip()` |
| Live corpus at time of writing: **68 tasks, 62 with a body, 74,180 characters total**; 25 bodies exceed 800 characters. One (task 37) sits at exactly 8,000 — a body clipped by the old bound, ending mid-word inside an unterminated fenced code block. | `sqlite3 db/regin.db "select count(*), sum(body is not null and body != ''), sum(length(body)) from agent_tasks"` |
| It is markdown *by convention only*. In practice it also holds agent-pasted XML (`<issue identifier="CAI-211"><title>…</title><description>…`, tasks 22 and 23), raw ProseMirror JSON pasted as text (task 22), column-aligned terminal output, unfenced shell commands with `\` continuations, and HTML comments used as structural markers (`<!-- one line per closed ticket -->`, tasks 56 and 66). | the rows themselves |

## The readers, named

Every one of these takes `body` as an opaque string today. They are the price list for changing what it holds.

| Call site | What it does with `body` | What breaks if it stops being markdown text |
|---|---|---|
| `lib/tasks/runs.py` `_prompt` (reached via `prompt_for`) | `body = (task.get("body") or "").strip()`, then **`text[:PROMPT_MAX]`** with `PROMPT_MAX = 8000`. That string *is* the launch prompt, concatenated with `_invocation`, `_tracker_block`, `_finish_block`. | A character slice of ProseMirror JSON is **syntactically invalid JSON** handed to a model as its whole instruction. The slice would have to become a doc-walking truncator, and the prompt a render step. This is the sharpest single cost. |
| `lib/tasks/store.py` `serialize` | `"body": task.body` — verbatim into every API response, and thus into `TaskDetailView`, `TaskEditSheet`, `TasksView`, `LiveTaskSheet`, the composer, and every HTTP/MCP capture client. | Every consumer learns the new type at once; there is no per-surface migration. |
| `lib/tasks/store.py` `update_task` | `row.body = fields["body"]` — a straight assignment, no normalisation. | Fine either way; it is the validator above it that types the value. |
| `lib/tasks/capture.py` `parse_capture` and **`parse_update`** | `_string(payload, "body", CAPTURE_BODY_MAX / BODY_MAX, required=False)` — asserts `isinstance(value, str)`, strips, and **refuses** over the bound. | ProseMirror JSON is an object, not a string. Either `_string` is replaced by a schema validator, or the wire format double-encodes JSON into a string and the bound starts counting JSON bytes (see *the 64,000 bound*, below). |
| `web/blueprints/tasks.py` `api_update_task` (PATCH, `@require_editor`) and `api_bulk_update_tasks` | Both funnel through `capture.parse_update`. | Same as above; the bulk path additionally means one malformed document can be sent against a whole selection. |
| `web/blueprints/tasks.py` `api_capture_task` (`require_scope('task:capture')`) and the authenticated composer route | `store.capture_task(**fields)` under the 8,000 bound. | This is the surface **agents and integrations write through**. Accepting only ProseMirror JSON means every agent that has ever written markdown into this field now emits a document tree. |
| `lib/tasks/query.py` (list/search) | `AgentTask.title.like(pattern) \| AgentTask.body.like(pattern)` — a raw SQL `LIKE` over the column. | Searching JSON matches key names and escape sequences; `"body"` and `"text"` hit every row. Search would need its own extracted-plaintext column or an FTS table. |
| `lib/tasks/integrations/regin.py` `_to_issue` | `description=clamp_description(task.body)` → `DESCRIPTION_MAX = 64_000` in `lib/tasks/integrations/base.py`. This is what regin hands a tracker when a task is exposed as an external issue. | Linear's description field is markdown. A ProseMirror document would have to be serialised here anyway — meaning markdown serialisation becomes a **server-side, Python** dependency, where no maintained ProseMirror serialiser exists. |
| `cli/commands/tasks.py` `_render_task` (`regin tasks show`) | `print(task["body"])` — the body *is* the terminal output. | Prints a JSON blob. Needs a renderer. |
| `frontend/src/views/TaskDetailView.vue` | `<MarkdownContent :markdown="task.body" />` | `MarkdownContent` is `marked` 18.0.3 + `v-html`; it takes a markdown **string**. A JSON body needs a second renderer or a client-side serialiser — and `MarkdownContent` is shared across memory, inbox, patterns, trace cards and present-mode (`grep -rl MarkdownContent frontend/src`), which map 66 puts explicitly out of scope. |
| `frontend/src/composables/useTaskEdit.js` | `body: task.body \|\| ''` … `body: draft.value.body.trim()` | String assumptions in the draft model. |

## Which markdown path is actually maintained

**`tiptap-markdown` is deprecated by its own author.** The first line of its README on `main` is a warning: *"Tiptap released a markdown extension in 3.7.0, please prefer using the official extension over this package. I don't plan to address current issues / PR."* ([aguingand/tiptap-markdown README](https://github.com/aguingand/tiptap-markdown/blob/main/README.md)). Latest publish 0.9.0 on 2025-09-08; last code commit 2025-09-08; 22 open issues, 3 open PRs.

**`@tiptap/markdown` is the maintained path.** First-party, MIT, in the `ueberdosis/tiptap` monorepo; first released as part of TipTap 3.7.0 on 2025-10-14, latest 3.31.3 on 2026-09-04 ([tiptap.dev/docs/editor/markdown](https://tiptap.dev/docs/editor/markdown), [announcement](https://tiptap.dev/blog/release-notes/introducing-bidirectional-markdown-support-in-tiptap)). Its only runtime dependency is `marked` ^17 — the same library `MarkdownContent` already renders with (at 18.0.3). Nothing about a markdown-persisted TipTap editor is paid: only `@tiptap-pro/*` (Conversion, Content AI, Comments, collab server) is commercial, and Conversion's markdown support is not needed here.

TipTap's own docs state two limitations that matter to this column: *"Comments are not supported yet"* and, for tables, *"only one child node per cell is allowed as the Markdown syntax can't represent multiple child nodes."*

**`prosemirror-markdown` directly is not a candidate.** It is alive (1.13.7, 2026-08-31, Marijn Haverbeke) but its GitHub repo is **archived** — issues moved to a self-hosted forge — and its default schema is CommonMark only: 12 nodes, 4 marks, **no tables, no task lists, no strikethrough, no inline HTML**. Its serializer *throws* on an unmapped node type unless `strict: false`. Using it means hand-writing every mapping TipTap already ships.

**`@tiptap/static-renderer`** (3.31.3, MIT) does exist and exposes `renderToMarkdown` for rendering stored JSON without an editor — relevant only under JSON-as-truth, and its docs warn it *"does not validate the markdown output."*

## The round-trip test

**Method.** All 25 bodies with `length(body) > 800` were exported from `db/regin.db` and run `markdown → TipTap document → markdown`, in `jsdom`, under both candidate paths, with `StarterKit` + `@tiptap/extension-table` + `@tiptap/extension-image` + `@tiptap/extension-list`. Diffs are byte-level (`cmp` / `diff`), not eyeballed. Libraries were installed into `/tmp`; **nothing was added to `frontend/package.json`**.

### Headline

| | Bodies byte-identical | Changed source lines (of 651) |
|---|---|---|
| `tiptap-markdown` 0.9.0, default config | **1 / 25** | **236 (36.3 %)** |
| `tiptap-markdown` 0.9.0, `bulletListMarker: '*'` | 4 / 25 | 87 (13.4 %) |
| **`@tiptap/markdown` 3.31.3, default config** | 4 / 25 | 166 (25.5 %) |
| **`@tiptap/markdown` 3.31.3, `*` list marker restored** | **8 / 25** | **71 (10.9 %)** |

Restoring the `*` bullet marker is the single highest-leverage change and costs three lines. The deprecated package exposes it as a config option (`bulletListMarker`); the official one hardcodes `"- "` in `@tiptap/extension-list`'s `listItem.renderMarkdown`, but `renderMarkdown` is an overridable extension field (`getExtensionField(extension, 'renderMarkdown')` in `@tiptap/markdown`), so:

```js
const base = ListItem.config.renderMarkdown
const ListItemStarMarker = ListItem.extend({
  renderMarkdown(node, h, ctx) { return base(node, h, ctx).replace(/^- /gm, '* ') },
})
```

Everything below is measured on **`@tiptap/markdown` with that override** — the maintained path, best-configured.

### What changed, per construct

| Construct | Verdict | Evidence |
|---|---|---|
| **Fenced code blocks** (```` ```bash ````, ```` ```python ````, bare) holding absolute paths, `grep --include=` flags, `&&` chains | **Survives byte-identical**, info string included. | probe; tasks 37, 42, 63 |
| **Hard-wrapped paragraphs** | **Survive** — the official path does not reflow. (`tiptap-markdown` joins them into one long line.) | probe; tasks 22, 23 |
| **Column-aligned plain text** | **Survives** (`Element:   div…` keeps its padding). `tiptap-markdown` collapses it. | tasks 22, 23 |
| **Nested lists** | Structure survives to 3 levels, ordered and unordered; markers rewritten `*` → `-` unless the override above is applied. | probe; 105 bullets across the corpus |
| **Tables** | Content survives; the delimiter row and every cell are **re-padded to aligned column width**, so a compact table is rewritten wholesale. | task 37; probe |
| **Blockquotes** | Survive, nesting and soft wraps included. | probe |
| **Task lists** `- [ ]` | Survive. | probe |
| **Images** `![alt](src "title")` | Survive, title attribute included. | probe |
| **Two-space hard break** | Survives. (`tiptap-markdown` rewrites it to a trailing `\`.) | probe |
| **Headings** | ATX survives. **Setext is rewritten to ATX** (`Foo\n===` → `# Foo`). | probe |
| **Literal `*` and `_`** | **Backslash-escaped**: `2 * 3` → `2 \* 3`; `[12.5px]` → `\[12.5px\]`. Worse than `prosemirror-markdown`, which exempts intra-word `_`: the official path escapes `snake_case_name` → `snake\_case\_name`. | probe; tasks 22, 23 |
| **Literal `#`** | Survives mid-line. | probe |
| **Links** | Inline links survive. **`](<url>)` loses its angle brackets** — and when the destination contains a space the result is *invalid markdown* (`](https://example.com/a b)`). `<https://autolink>` is rewritten to `[url](url)`. **Reference links `[x][1]` are inlined and the `[1]: …` definition deleted.** | probe; tasks 37, 42 |
| **HTML comments `<!-- -->`** | **Deleted outright.** TipTap documents this: *"Comments are not supported yet."* | tasks 56 and 66 — including map 66's own `<!-- one line per closed ticket -->` |
| **Raw HTML / XML blocks** | Two behaviours, both wrong. An **unknown** tag (`<issue identifier="…">`) is **entity-escaped into visible text** (`&lt;issue identifier="…"&gt;`) — recoverable but garbage on screen. A **known** HTML block (`<details>/<summary>`) has its tags **deleted** and its text flattened into the neighbouring paragraph. | tasks 22, 23; probe |
| **Indented (4-space) code blocks** | Converted to fenced. | probe |
| **Trailing newline** | Dropped — a no-op, since `_string` already strips. | `lib/tasks/capture.py` |

Note what is **not** on this list under the official path: emphasis spanning a code span (`**A \`x\` B**`) now survives intact. Under `tiptap-markdown` it splits into `**A** \`x\` **B**` and was that path's largest residual class.

### Is the damage bounded?

Almost. A second round trip leaves **24 of 25 byte-identical to pass 1**, and pass 3 is identical to pass 2 for all 25.

The one that moves is task 22, and it moves for an interesting reason: pass 1 leaves a bare URL inside a pasted ProseMirror-JSON blob sitting in running text, and pass 2's GFM **autolinker wraps the entire line in `[…](…)`**. That is a compounding rewrite, not a settling one; it is suppressible via `markdownOptions: { gfm: false }` at the cost of GFM tables and strikethrough, or by keeping such content fenced.

**So the normalisation is effectively a one-time event per body, not unbounded drift.** But it is all-or-nothing: `editor.getMarkdown()` re-serialises the *whole* document, so editing one word in task 66 rewrites every line the serializer would touch. And HTML comments are a **deletion**, not a reformatting. That is data loss in a column that holds specs.

### Bundle cost, measured

Bundled from the `/tmp` install with `esbuild --bundle --minify --format=esm`, gzipped with `gzip -9`:

| Stack | minified | gzip |
|---|---|---|
| `@tiptap/core` + `starter-kit` + `extension-table` + `extension-image`, no markdown | 437,294 B | 137,213 B |
| …plus **`@tiptap/markdown`** (`marked`) | 499,936 B | **156,431 B** (+19,218) |
| …plus `tiptap-markdown` (`markdown-it` + `prosemirror-markdown`) | 615,503 B | 199,205 B (+61,992) |
| `marked` 18.0.3 alone, for scale | 41,357 B | 12,420 B |

The maintained path is **~43 kB gzip cheaper** than the deprecated one, because `marked` is a third of `markdown-it` + `prosemirror-markdown` — and `marked` is already in the bundle. For scale, the app's whole built `web/static/dist` is 3.4 MB across its chunk set; 156 kB gzip is large but route-splittable, loading only on `/tasks/<id>` when the operator clicks Edit.

## Pricing ProseMirror-JSON-as-truth

### The 64,000 bound stops fitting the artifact it was raised for

Measured over the corpus: **markdown 63,646 chars → ProseMirror JSON 146,290 chars, a 2.30× inflation** (worst case task 37 at 2.77×). The 8,000-character task 37 becomes 22,088 characters of JSON.

`BODY_MAX` was raised to 64,000 with the explicit reasoning that *"the largest real artifact measured is 29,488 characters, so the bound has to clear 32,000 rather than sit at it."* To test that directly, the first 29,488 characters of `docs/trace-transcript-relationships-design.md` — a real spec of exactly that size — were parsed:

```
29,293 chars of markdown  →  82,485 chars of ProseMirror JSON
```

**82,485 > 64,000.** Under JSON-as-truth the bound would reject the exact document it was sized to accept, and `_string` refuses rather than truncating, so the operator gets a 400. Every bound in the chain (`BODY_MAX`, `CAPTURE_BODY_MAX`, `DESCRIPTION_MAX`, `PROMPT_MAX`) would need re-deriving against a unit it no longer measures — and "64,000 characters" stops being a number a human can reason about, because it no longer counts the characters they typed.

*(A related hazard exists even under markdown-as-truth: that same 29,293-character spec comes back from the round trip at 34,048 characters — 16 % larger, mostly table padding. A body near the bound can be pushed over it by the normalisation itself and rejected on save.)*

### The other concrete costs

1. **`_prompt`'s `text[:PROMPT_MAX]`** becomes a doc-walking truncator plus a markdown renderer, both of which must exist **in Python** — there is no maintained Python ProseMirror serialiser. The prompt's stated contract ("truncation is a property of the captured idea") has to be re-expressed in nodes.
2. **`clamp_description(task.body)`** hands a tracker a description, and Linear's field is markdown. So markdown serialisation is needed server-side regardless — JSON-as-truth buys a *second* serialiser, not zero.
3. **`AgentTask.body.like()`** silently degrades: every row contains `"type"`, `"text"`, `"content"`.
4. **`regin tasks show`** prints the blob.
5. **`MarkdownContent`** is the wrong renderer, and map 66 puts replacing it repo-wide out of scope.
6. **`_string`'s `isinstance(value, str)`** rejects an object, so the wire format is either double-encoded JSON-in-a-string or a rewritten validator.
7. **The 62 existing bodies need a migration** — and that migration *is* the lossy conversion measured above, applied to every row at once, including the two that lose their HTML-comment markers and the two whose `<issue>` wrappers become escaped text.
8. **Every agent that has ever written this field** writes markdown through `POST /api/tasks`. They would all have to emit ProseMirror documents, or a markdown→JSON shim would have to live on the capture surface — in which case markdown is still the interchange format and JSON is just a storage detail bought at 2.3× the bytes.

**Verdict: ProseMirror-JSON-as-truth is strictly worse here.** It breaks the bound that was raised on purpose, it needs a Python markdown serialiser *anyway* for the tracker path, and its migration is the same lossy conversion as markdown-as-truth plus a data-type change across every call site in the table above.

## The escape hatch

**A raw-markdown toggle is possible, and it is cheap.** TipTap emits `update` only when a transaction actually changed the document — `@tiptap/core` bails on `!transactions.some(tr => tr.docChanged) || prevState.doc.eq(state.doc)`. So:

- **Open-and-close writes nothing.** Gate the PATCH on `onUpdate` having fired, not on comparing serialised text. A body only normalises if a human typed into the WYSIWYG for that task.
- **A raw mode is a second editing surface over the same column.** A plain `Textarea` — what `TaskEditSheet.vue` already is — writes bytes through untouched. Toggling *to* raw loses nothing.

**What the toggle loses:** it does not make the WYSIWYG lossless, it only makes the loss opt-in per task. A body holding `<!-- -->` markers, an `<issue>` wrapper, or a pasted JSON blob is one accidental WYSIWYG keystroke away from losing them, with no per-construct warning. Two mitigations are cheap and belong in the spec: (a) **detect-and-default** — scan the body on load for `<!--`, a raw HTML/XML block, or an unfenced bare URL, and open *that* body in raw mode with a note rather than silently in WYSIWYG; (b) keep the pre-edit body for an undo, since the normalisation arrives as a single PATCH.

## Anything already in the tree

Nothing in `frontend/package.json` clears the bar.

- **`reka-ui` 2.9.10 — no editor.** Its component inventory (read from the installed `dist/index.d.ts`) has `Editable`, `TagsInput` and `NumberField` but no rich-text anything; `Editable` is documented as *"an input field used for editing a single line of text"* ([reka-ui.com/docs/components/editable](https://reka-ui.com/docs/components/editable)). Grepping the type surface for `RichText|Editor|contenteditable` returns zero.
- **`primevue` 4.5.5 — has an `Editor`, but it is not free and not markdown.** It wraps Quill, which PrimeVue neither bundles nor declares: its `package.json` lists no `quill` in dependencies, peers or optional peers, and the docs say *"Editor uses Quill editor underneath so it needs to be installed as a dependency — `npm install quill`"* ([primevue.dev/editor](https://primevue.dev/editor/)). So adopting it is a new-dependency decision either way. Its `v-model` is an **HTML string** (`quill.getSemanticHTML()` on every change, per `primevue/editor/index.mjs`), not markdown and not the Delta; Quill's own docs describe Delta as the native format that avoids *"the ambiguity and complexity of HTML"* ([quilljs.com/docs/delta](https://quilljs.com/docs/delta)). Reaching markdown from there needs `turndown` as a *second* new dependency, and the Quill-markdown ecosystem is dead — `quilljs-markdown` last published 2022-07-21, `quill-delta-to-markdown` 2019-01-23.
- **`marked` 18.0.3 — one-way by design.** Its complete export surface is `marked, Marked, parse, parseInline, parser, lexer, use, walkTokens, options, setOptions`; there is no HTML→markdown path and no plan for one ([marked.js.org](https://marked.js.org/)). It stays the *renderer* — but note it is also the parser `@tiptap/markdown` depends on, so the winning option below adds a library the tree already trusts.

## Recommendation

**Keep markdown as the truth in `body`. Use TipTap with the first-party `@tiptap/markdown`, the `*` list-marker override restored, mounted behind an explicit Edit and PATCHing only when TipTap's own `onUpdate` has fired — and close the HTML-comment hole before it ships.**

The column stays a markdown string, so *every* call site in the readers table keeps working unchanged: `_prompt`'s `text[:8000]` slice still produces a prompt, `clamp_description` still hands Linear markdown, `body.like()` still searches prose, `regin tasks show` still prints a document, `MarkdownContent` still renders it, and the 64,000 bound still counts characters a human typed. Nothing migrates. The price is a one-time, converged, opt-in normalisation of ~11 % of the lines of any body a human actually edits in the WYSIWYG — and the residue is dominated by three classes that render identically to what they replace: backslash escapes, table column padding, and `](<url>)` losing its angle brackets.

Three things must be designed around rather than accepted:

1. **HTML comments are deleted.** TipTap says so in its own docs. This matters here because `<!-- one line per closed ticket -->` is a *structural marker on the wayfinder maps themselves* (tasks 56 and 66). Strip-and-reattach them outside the editor, or fall back to raw mode for any body containing one.
2. **Raw HTML/XML blocks are escaped or flattened.** Agent-pasted `<issue identifier="…">` wrappers (tasks 22, 23) come back as visible `&lt;issue…&gt;`. Detect on load, default to raw mode.
3. **A link destination containing a space becomes invalid markdown.** `](<https://example.com/a b>)` → `](https://example.com/a b)`. Worth a pre-save assertion, since image `src`s are about to become a body concern under map 66's constraint 2.

**Runner-up: a markdown-source editor — CodeMirror 6 with the markdown language package, over a `marked` preview.** It is the only option with a *zero* round trip: bytes you do not touch are bytes that stay, so `<!--` markers, `<issue>` wrappers, column alignment and hard wraps all survive by construction, there is no normalisation event to gate, warn about or undo, and it is smaller.

**Why TipTap wins anyway:** map 66's destination is a Linear-grade detail page with *inline images rendered in place* and formatting the operator never types — constraint 2 ("Images are both gallery and inline") and constraint 3 ("an inline reference drives delivery") are WYSIWYG requirements, and a source editor answers neither. CodeMirror gives up the entire stated goal to buy back ~11 % of lines in the bodies a human edits, across a corpus of 62 rows. Take TipTap on the maintained extension, take the one-time normalisation, and pay for the deletion holes with a detect-and-default raw mode.

**Not recommended:** `tiptap-markdown` (deprecated by its author, 43 kB gzip more expensive, and it loses hard wraps, column alignment and emphasis-around-code that the official path keeps), `prosemirror-markdown` directly (archived repo, CommonMark-only schema with no tables or task lists, every mapping hand-written), and PrimeVue `Editor` (a new `quill` dependency, HTML-not-markdown storage, and a dead markdown ecosystem around it).

---

*Reproduction: bodies exported with `sqlite3 db/regin.db "select id, body from agent_tasks where length(body) > 800"`; harness and per-body diffs under `/tmp/tbrt/` — throwaway, and nothing was added to `frontend/package.json`.*
