// The client half of full-text search: the local span pass, and the
// highlight primitives both panes render through.
//
// The local pass exists for latency only — it answers the first keystroke
// from spans already in memory while the authoritative server answer is in
// flight. It is NOT the corpus: the trace view loads shallowly and
// paginated, so `allSpans` holds a window of a long session, and the server
// group supersedes these rows by `span_id` the moment it lands.

export const MIN_QUERY_CHARS = 2
// `#` is an ADDRESS sigil, not a search term: what follows it is a trace-id
// prefix, so the palette resolves it against the session store instead of
// searching any corpus. Four characters is the floor — shorter than that the
// prefix is still mid-paste and would answer with unrelated sessions.
// `MIN_ID_CHARS` mirrors the server's constant of the same name in
// web/blueprints/trace/switcher.py; a client that asks below the floor gets an
// empty list back, so the two must agree or the hint contradicts the answer.
export const ID_SIGIL = '#'
export const MIN_ID_CHARS = 4

// Why a `#` lookup lists nothing: still too short to be an id, or no id has
// that prefix. `title` is empty for the second — it is a plain answer, not a
// state to wait out.
export function lookupNote(prefix) {
  if (prefix.length < MIN_ID_CHARS) {
    return {
      title: 'Keep typing the session id',
      body: `Session ids resolve from ${MIN_ID_CHARS} characters — “#${prefix}” could be several `
        + `sessions. Type ${MIN_ID_CHARS - prefix.length} more, or search the prompt text instead.`,
    }
  }
  return { title: '', body: `No session id starts with ${prefix}.` }
}

// The trace-id prefix a query addresses, or null if it is an ordinary search.
// Returns '' for a bare '#' — the reader has declared intent and has not
// finished typing, which is a different state from "not addressing anything".
export function idPrefixOf(trimmed) {
  const q = String(trimmed || '')
  return q.startsWith(ID_SIGIL) ? q.slice(ID_SIGIL.length).trim() : null
}
export const MAX_COUNTED_BODY_HITS = 9
export const TITLE_WEIGHT = 100
export const SUB_WEIGHT = 20
// Rows per group in the all-kinds list (R1). Spans get more because they are
// the session you are looking at; sessions one more than the rest because
// they are what the reader most often came for. Every other group overflows
// into a "Show all N" row that opens the kind's Drill.
export const SPAN_ROW_CAP = 14
export const SESSION_ROW_CAP = 4
export const GROUP_ROW_CAP = 3
// A Drill pages its one kind by `offset`, this many rows at a time.
export const DRILL_PAGE = 12

export function groupCap(kind) {
  if (kind === 'span') return SPAN_ROW_CAP
  return kind === 'session' ? SESSION_ROW_CAP : GROUP_ROW_CAP
}
export const PREVIEW_SOURCE_LINES = 60
export const PREVIEW_WINDOW_LINES = 34
export const SNIPPET_BEFORE = 16
export const SNIPPET_AFTER = 120
// The hit list is a triage surface, not the document: past this many lines
// the reader should open the page rather than keep scrolling a list.
export const HIT_LINE_CAP = 200
export const HIT_LEAD = 28
export const HIT_TRAIL = 150
// The width below which the preview becomes a sheet. Mirrored by the
// `@media (max-width: 900px)` blocks in the search components — change both.
export const NARROW_MAX_PX = 900

// Process first (R1): Feature → Task → Defect → Session, then the documents.
// `span` leads because it only ever appears under This session, where its
// sole neighbour is `file`.
export const KIND_ORDER = ['span', 'feature', 'task', 'defect', 'session', 'pattern', 'file']

// `tagStyle` picks how the row's right-hand meta renders, because the four
// kinds put genuinely different things there (spec §02): a span's category,
// a pattern's space slug, a session's span count, a file's ± diff. Rendering
// all four as one muted run made every row look alike.
export const KIND_META = {
  feature: {
    label: 'Features',
    accent: 'var(--search-accent-feature)',
    icon: 'flag',
    tagStyle: 'plain',
  },
  task: {
    label: 'Tasks',
    accent: 'var(--search-accent-task)',
    icon: 'list-checks',
    tagStyle: 'plain',
  },
  defect: {
    label: 'Defects',
    accent: 'var(--search-accent-defect)',
    icon: 'alert-triangle',
    tagStyle: 'plain',
  },
  span: {
    label: 'This session · spans',
    accent: 'var(--search-accent-span)',
    icon: 'terminal',
    tagStyle: 'category',
  },
  pattern: {
    label: 'Patterns & wiki',
    accent: 'var(--search-accent-pattern)',
    icon: 'tag',
    tagStyle: 'chip',
  },
  session: {
    label: 'Sessions',
    accent: 'var(--search-accent-session)',
    icon: 'workflow',
    tagStyle: 'plain',
  },
  file: {
    label: 'Files & diffs',
    accent: 'var(--search-accent-file)',
    icon: 'file',
    tagStyle: 'diff',
  },
}

const CATEGORY_TINTS = new Set([
  'prompt', 'thinking', 'assistant', 'tool',
  'edit', 'shell', 'rule', 'agent', 'system',
])

// A span tag is `category` or `category · compacted` (lib/fulltext/span_docs.py),
// so the tint keys off the leading segment only.
export function categoryTint(tag) {
  const head = String(tag || '').split('·', 1)[0].trim()
  const key = CATEGORY_TINTS.has(head) ? head : 'system'
  return `var(--search-cat-${key})`
}

// `_diff_tag` emits `+12 −3` with U+2212, and only when at least one side is
// non-zero. Returns null for anything that is not that shape, so a file row
// with no diff stats falls back to the plain tag instead of rendering "+0 −0".
const DIFF_TAG = /^\+(\d+)\s+−(\d+)$/

export function diffTagParts(tag) {
  const m = DIFF_TAG.exec(String(tag || '').trim())
  return m ? { adds: `+${m[1]}`, dels: `−${m[2]}` } : null
}

// Scope and kind are two axes, not one. Scope says WHERE to look; the facet
// chips say WHICH KIND of thing to show. Collapsing them — which is what the
// old ⌘F did by pinning the Spans chip — makes "everything in this session"
// and "spans anywhere" unsayable, and leaves chips that cannot have results.
export const SCOPES = [
  { value: 'session', label: 'This session' },
  { value: 'global', label: 'Everywhere' },
]

// Which kinds each scope can honestly answer. A pattern page and another
// session are not things that live *inside* one session, and "this session's
// spans" has no referent once the search is not about a session — the `session`
// kind IS the cross-session span answer, as a per-session digest. Only `file`
// is meaningful under both, and it means different sets: touched here vs.
// touched anywhere.
export const SCOPE_KINDS = {
  session: ['span', 'file'],
  global: ['feature', 'task', 'defect', 'session', 'pattern', 'file'],
}

const KIND_FACET_LABEL = {
  span: 'Spans', pattern: 'Patterns', session: 'Sessions', file: 'Files',
  feature: 'Features', task: 'Tasks', defect: 'Defects',
}

export const kindFacetLabel = kind => KIND_FACET_LABEL[kind] || 'All'

const KIND_NOUN = {
  span: ['span', 'spans'], pattern: ['pattern', 'patterns'],
  session: ['session', 'sessions'], file: ['file', 'files'],
  feature: ['feature', 'features'], task: ['task', 'tasks'],
  defect: ['defect', 'defects'],
}

export function kindNoun(kind, n) {
  const [one, many] = KIND_NOUN[kind] || ['result', 'results']
  return n === 1 ? one : many
}

// What a facet or group header prints for a kind whose answer is
// `{ status, total }`. The marks are the Invariants' vocabulary: `…` still
// loading, `!` this kind failed, `—` nothing could be asked at all.
export function countText(state, offline = false) {
  if (offline) return '—'
  if (state?.status === 'pending') return '…'
  if (state?.status === 'failed') return '!'
  return String(state?.total ?? 0)
}

// F146 / f:146 / T… / D… address one entity; a bare number addresses every
// kind that has the id. Mirrors `_ENTITY_REF` / `_NUMBER` in
// lib/palette/resolve.py — a query the server would not read as an address
// must not be sent there, or it comes back `address: null` with nothing shown.
const ENTITY_REF = /^([ftd]):?(\d{1,18})$/i
const BARE_NUMBER = /^\d{1,18}$/
const REF_KIND = { f: 'feature', t: 'task', d: 'defect' }

// The server reads the id as an integer, so `T0146` and `T146` name one task
// and the text it searches for is the normalised digits.
const digits = raw => raw.replace(/^0+(?=\d)/, '')

export function addressOf(trimmed) {
  const q = String(trimmed || '')
  const ref = ENTITY_REF.exec(q)
  if (ref) return { kind: REF_KIND[ref[1].toLowerCase()], needle: digits(ref[2]) }
  if (BARE_NUMBER.test(q)) return { kind: 'number', needle: digits(q) }
  return null
}

// A row is held by kind AND id: feature, task and defect ids are all small
// integers, so F146, T146 and D146 share `id` and would share a cursor.
export const rowKey = doc => `${doc.kind}:${doc.id}`
export const rowDomId = key => `search-row-${String(key).replace(':', '-')}`

const REF_ROUTE = { F: 'features', T: 'tasks', D: 'defects' }

// A resolver row carries its `ref` and `id`, not a document: the entity
// route is read back off them.
export function rowDoc(row) {
  const m = /^([FTD])(\d+)$/.exec(row.ref || '')
  const id = String(row.id ?? (m ? m[2] : ''))
  return {
    kind: row.kind,
    id,
    title: row.title,
    sub: row.context || '',
    body: row.context || '',
    href: m ? `/${REF_ROUTE[m[1]]}/${m[2]}` : `/trace/sessions/${encodeURIComponent(id)}`,
    row,
  }
}

export function facetsFor(scope) {
  const kinds = SCOPE_KINDS[scope] || SCOPE_KINDS.global
  return [
    { label: 'All', kind: null },
    ...kinds.map(kind => ({ label: KIND_FACET_LABEL[kind], kind })),
  ]
}

const PROSE_KEYS = {
  prompt: ['expanded_text', 'text'],
  assistant_response: ['text'],
  'assistant.thinking': ['thinking_text'],
  'harness.recap': ['content'],
  'task.notification': ['result', 'summary'],
  'tool.failure': ['error'],
}

const EXACT_CATEGORIES = {
  prompt: 'prompt',
  'assistant.thinking': 'thinking',
  assistant_response: 'assistant',
  'rule.check': 'rule',
}

const PREFIX_CATEGORIES = [
  ['tool.Edit', 'edit'], ['tool.Write', 'edit'],
  ['tool.MultiEdit', 'edit'], ['tool.NotebookEdit', 'edit'],
  ['tool.Bash', 'shell'],
  ['tool.Task', 'agent'], ['agent.', 'agent'],
  ['tool.', 'tool'],
]

const MONO_CATEGORIES = new Set(['shell', 'edit', 'tool'])

// Mirrors lib/fulltext/span_docs.py::category_of. The two must agree or a
// row's tag would change under it when the server answer replaces the local
// one.
export function categoryOf(name) {
  if (EXACT_CATEGORIES[name]) return EXACT_CATEGORIES[name]
  for (const [prefix, category] of PREFIX_CATEGORIES) {
    if (name.startsWith(prefix)) return category
  }
  return 'system'
}

function proseOf(span) {
  const a = span.attributes || {}
  for (const key of PROSE_KEYS[span.name] || []) {
    if (typeof a[key] === 'string' && a[key]) return a[key]
  }
  if (a.server_side && typeof a.response_text === 'string') return a.response_text
  if (typeof a.user_message === 'string') return a.user_message
  return ''
}

function ruleLines(a) {
  return (a.applicable_rules || [])
    .filter(r => r && typeof r === 'object')
    .map(r => `${r.violated ? '✗' : '✓'} ${r.id || ''}  ${r.summary || ''}`.trimEnd())
}

const str = v => (typeof v === 'string' ? v : '')

export function spanBody(span) {
  const a = span.attributes || {}
  if (a.redacted || a.content_redacted) return 'content redacted'
  return [
    proseOf(span),
    str(a.file_path),
    str(a.diff),
    str(a.command) || str(a.command_preview),
    str(a.stdout),
    str(a.stderr),
    str(a.description),
    str(a.summary),
    str(a.pattern),
    str(a.rule_file),
    ruleLines(a).join('\n'),
  ].filter(Boolean).join('\n')
}

// Mirrors lib/fulltext/span_docs.py::_subject — one identifying detail per
// tool, so a list of nine `tool.Bash` rows stays scannable.
function spanSubject(a) {
  if (a.file_path) return a.file_path
  const command = str(a.command) || str(a.command_preview)
  if (command) return command.trim().split('\n', 1)[0].slice(0, 80)
  return str(a.description).slice(0, 80)
}

export function spanToDoc(span) {
  const a = span.attributes || {}
  const category = categoryOf(span.name || '')
  const subject = spanSubject(a)
  const bits = []
  if (Number.isInteger(a.turn_index)) bits.push(`turn #${a.turn_index}`)
  if (span.duration_ms) {
    const secs = span.duration_ms / 1000
    bits.push(secs < 60 ? `${secs.toFixed(1)}s` : `${(secs / 60).toFixed(1)}m`)
  }
  if (subject) bits.push(subject)
  const crumb = []
  if (Number.isInteger(a.turn_index)) crumb.push(`turn #${a.turn_index}`)
  if (span.agent_id) crumb.push('subagent')
  crumb.push(span.name || '')
  if (subject) crumb.push(subject)
  return {
    kind: 'span',
    id: span.span_id,
    // The span NAME, not spanLabel's rich one-liner (spec §02). The server
    // sends the same, and a row whose title changed the moment the remote
    // answer superseded the local one read as a different result.
    title: span.name || '',
    sub: bits.join(' · '),
    tag: a.compacted ? `${category} · compacted` : category,
    crumb: crumb.join(' · '),
    body: spanBody(span),
    mono: MONO_CATEGORIES.has(category),
    local: true,
    // The preview renders the span itself through the presentation renderers,
    // which are pure over the span object and cannot work from the flattened
    // body. Carried only on local rows; a server row resolves by id instead.
    span,
  }
}

export function countHits(text, query) {
  if (!text || !query) return 0
  const hay = String(text).toLowerCase()
  const needle = query.toLowerCase()
  let count = 0
  let at = hay.indexOf(needle)
  while (at > -1) {
    count += 1
    at = hay.indexOf(needle, at + needle.length)
  }
  return count
}

// Mirrors lib/fulltext/docs.py::score_doc.
export function scoreDoc(doc, query) {
  const needle = query.toLowerCase()
  const inTitle = (doc.title || '').toLowerCase().includes(needle)
  const inSub = (doc.sub || '').toLowerCase().includes(needle)
  const hits = countHits(doc.body, query)
  if (!inTitle && !inSub && !hits) return null
  return {
    doc: { ...doc, bodyHits: hits },
    score: (inTitle ? TITLE_WEIGHT : 0) + (inSub ? SUB_WEIGHT : 0)
      + Math.min(hits, MAX_COUNTED_BODY_HITS),
    hits,
  }
}

export function searchLocalSpans(spans, query) {
  if (!query || query.length < MIN_QUERY_CHARS) return []
  const out = []
  for (let i = 0; i < spans.length; i += 1) {
    const scored = scoreDoc(spanToDoc(spans[i]), query)
    if (scored) out.push({ ...scored, order: i })
  }
  return out.sort((a, b) => (b.score - a.score) || (a.order - b.order))
}

// ── Highlighting ────────────────────────────────────────────
//
// Both helpers return `{ pre, hit, post, has }` and NEVER a zero-length
// `hit`: an empty <mark> renders as a 2px amber sliver with nothing in it.
// Callers gate the <mark> on `has`.

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function snippet(text, query, before = SNIPPET_BEFORE, after = SNIPPET_AFTER) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim()
  if (!query) return { pre: clip(flat, after), hit: '', post: '', has: false }
  const at = flat.toLowerCase().indexOf(query.toLowerCase())
  if (at < 0) return { pre: clip(flat, after), hit: '', post: '', has: false }
  const from = Math.max(0, at - before)
  return {
    pre: (from > 0 ? '…' : '') + flat.slice(from, at),
    hit: flat.slice(at, at + query.length),
    post: clip(flat.slice(at + query.length), after),
    has: true,
  }
}

// Whitespace is preserved verbatim — a diff or a shell log must not reflow.
export function markLine(line, query) {
  const text = String(line ?? '')
  if (!query) return { pre: text, hit: '', post: '', has: false }
  const at = text.toLowerCase().indexOf(query.toLowerCase())
  if (at < 0) return { pre: text, hit: '', post: '', has: false }
  return {
    pre: text.slice(0, at),
    hit: text.slice(at, at + query.length),
    post: text.slice(at + query.length),
    has: true,
  }
}

export function previewWindow(body, query, hitIndex = 0) {
  const lines = String(body || '').split('\n').slice(0, PREVIEW_SOURCE_LINES)
  let target = -1
  if (query) {
    const needle = query.toLowerCase()
    let seen = -1
    for (let i = 0; i < lines.length; i += 1) {
      if (!lines[i].toLowerCase().includes(needle)) continue
      seen += 1
      if (seen >= hitIndex) { target = i; break }
    }
  }
  const from = target > 12 ? target - 6 : 0
  return {
    lines: lines.slice(from, from + PREVIEW_WINDOW_LINES),
    elidedAbove: from,
    truncated: lines.length > from + PREVIEW_WINDOW_LINES,
    // Where the walked hit sits INSIDE the returned window, so the renderer
    // can mark that one line apart from the others and scroll it into view.
    cursor: target < 0 ? -1 : target - from,
  }
}

// ── Prose bodies ─────────────────────────────────────────────
//
// A wiki page or a pattern is markdown; a diff, a shell log or a file body
// is not. The palette printed both through one raw-line renderer, which is
// why a wiki page read as `## Symptom` instead of a heading. These helpers
// are the split: they parse the markdown a prose body is written in, and
// keep each block's line span so the hit stepper — which addresses LINES —
// can still say which block it is parked in.

const HEADING_RE = /^(#{1,6})\s+(.*)$/
const BULLET_RE = /^\s*[-*]\s+(.*)$/
const STRONG_RE = /\*\*([^*]+?)\*\*/g

// What the reader will SEE on that line. The hit list cites lines out of a
// prose body, and quoting `## Symptom` at someone who will land on a heading
// rendered as SYMPTOM makes the two views disagree about the same line.
export function plainLine(text, literal) {
  if (literal) return String(text ?? '')
  return String(text ?? '')
    .replace(HEADING_RE, '$2')
    .replace(STRONG_RE, '$1')
    .replace(/`/g, '')
}

// Every line of the body the query occurs on, clipped around the term. This
// is the unit the preview's stepper walks and the hit list renders, and it is
// deliberately NOT the occurrence count the row badge shows: a line holding
// three occurrences is one place to look, not three.
export function hitLines(body, query, cap = HIT_LINE_CAP, { plain = false } = {}) {
  if (!query) return []
  const needle = query.toLowerCase()
  const src = String(body || '').split('\n')
  const out = []
  for (let i = 0; i < src.length && out.length < cap; i += 1) {
    if (!src[i].toLowerCase().includes(needle)) continue
    const text = plain ? plainLine(src[i], false) : src[i]
    out.push({ line: i + 1, index: i, text, ...snippet(text, query, HIT_LEAD, HIT_TRAIL) })
  }
  return out
}

export function proseBlocks(body) {
  const lines = String(body || '').split('\n')
  const out = []
  let para = null
  const flush = () => { if (para) { out.push(para); para = null } }
  lines.forEach((line, i) => {
    if (!line.trim()) { flush(); return }
    const heading = line.match(HEADING_RE)
    if (heading) {
      flush()
      out.push({
        type: 'h',
        depth: heading[1].length,
        text: plainLine(heading[2], false),
        from: i,
        to: i,
      })
      return
    }
    const bullet = line.match(BULLET_RE)
    if (bullet) {
      flush()
      out.push({ type: 'li', text: bullet[1], from: i, to: i })
      return
    }
    // A hard-wrapped paragraph is one paragraph, not six lines. Joining is
    // what lets it reflow to the pane instead of breaking at the width the
    // author's editor happened to be.
    if (para) { para.text += ` ${line.trim()}`; para.to = i }
    else para = { type: 'p', text: line.trim(), from: i, to: i }
  })
  flush()
  return out
}

// The inline markdown regin's own bodies actually use: backticked code and
// `**bold**`, which NEST — ``**`session_spans`**`` is the commonest shape in
// the wiki. Split rather than replaced, so the query mark can still be placed
// INSIDE a run instead of being swallowed by an innerHTML pass.
//
// Emphasis is tokenised FIRST and code inside it second. The other order
// leaves the two `**` on opposite sides of a code token, where nothing can
// pair them, and the reader sees literal asterisks.
const CODE_SPLIT = /(`[^`]+`)/
const STRONG_SPLIT = /(\*\*[^*]+?\*\*)/

export function inlineSpans(text) {
  const out = []
  String(text ?? '').split(STRONG_SPLIT).filter(Boolean).forEach((part) => {
    const strong = part.length > 4 && part.startsWith('**') && part.endsWith('**')
    const inner = strong ? part.slice(2, -2) : part
    inner.split(CODE_SPLIT).filter(Boolean).forEach((run) => {
      const code = run.length > 2 && run.startsWith('`') && run.endsWith('`')
      out.push({ code, strong, text: code ? run.slice(1, -1) : run })
    })
  })
  return out
}

// Every occurrence, not just the first. `snippet` answers "show me the match"
// for a one-line row; a rendered paragraph has to mark all of them or the
// reader counts three hits in the badge and finds one in the text.
export function splitOnQuery(text, query) {
  const src = String(text ?? '')
  if (!query) return [{ text: src, hit: false }]
  const needle = query.toLowerCase()
  const hay = src.toLowerCase()
  const out = []
  let from = 0
  let at = hay.indexOf(needle)
  while (at > -1) {
    if (at > from) out.push({ text: src.slice(from, at), hit: false })
    out.push({ text: src.slice(at, at + query.length), hit: true })
    from = at + query.length
    at = hay.indexOf(needle, from)
  }
  if (from < src.length) out.push({ text: src.slice(from), hit: false })
  return out
}

export function caretAtEnd(input) {
  if (input?.selectionStart == null) return true
  const end = input.value.length
  return input.selectionStart === end && input.selectionEnd === end
}

// One rail chip as the palette shows it. In the switcher the Sessions chip
// counts the ROSTER, not the search corpus: with an empty query the corpus is
// empty by definition, so the chip would read 0 beside a list of twelve rows.
// With nothing typed no kind was asked anything, so a count would be a 0 that
// means "not searched" beside a disabled chip that means "searched, none".
export function railFacet(f, { switcher, held, rosterCount, activeLabel }) {
  const roster = switcher && f.kind === 'session'
  const unasked = held && !roster && !f.text
  let count = f.text
  if (roster) count = String(rosterCount)
  else if (unasked) count = ''
  return {
    ...f,
    icon: KIND_META[f.kind]?.icon || 'list',
    active: f.label === activeLabel,
    count,
    disabled: !roster && !unasked && f.disabled,
  }
}
