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
// Rows per group. Spans get more because they are the session you are
// looking at; everything else is context.
export const SPAN_ROW_CAP = 14
export const OTHER_ROW_CAP = 6
export const PREVIEW_SOURCE_LINES = 60
export const PREVIEW_WINDOW_LINES = 34
export const SNIPPET_BEFORE = 16
export const SNIPPET_AFTER = 120
// The width below which the preview becomes a sheet. Mirrored by the
// `@media (max-width: 900px)` blocks in the search components — change both.
export const NARROW_MAX_PX = 900

export const KIND_ORDER = ['span', 'pattern', 'session', 'file']

// `tagStyle` picks how the row's right-hand meta renders, because the four
// kinds put genuinely different things there (spec §02): a span's category,
// a pattern's space slug, a session's span count, a file's ± diff. Rendering
// all four as one muted run made every row look alike.
export const KIND_META = {
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
  global: ['pattern', 'session', 'file'],
}

const KIND_FACET_LABEL = {
  span: 'Spans', pattern: 'Patterns', session: 'Sessions', file: 'Files',
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

export function previewWindow(body, query) {
  const lines = String(body || '').split('\n').slice(0, PREVIEW_SOURCE_LINES)
  let first = -1
  if (query) {
    const needle = query.toLowerCase()
    first = lines.findIndex(l => l.toLowerCase().includes(needle))
  }
  const from = first > 12 ? first - 6 : 0
  return {
    lines: lines.slice(from, from + PREVIEW_WINDOW_LINES),
    elidedAbove: from,
    truncated: lines.length > from + PREVIEW_WINDOW_LINES,
  }
}
