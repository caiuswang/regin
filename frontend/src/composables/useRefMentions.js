// `@`-triggered references for the body composers: a spec, a step's
// instruction, a task's details. One menu over six kinds, searched through
// `GET /api/refs`, which owns the query grammar (lib/refs/search.py).
//
// Mirrors useFileMentions.js: a backwards token scan from the caret, a
// debounced search, a sequence guard, a stale list that stays drawn but is
// never accepted, and an armed accept for an Enter pressed into that window.
// What differs is inherent to a markdown body rather than a terminal prompt:
//   - the trigger must stay quiet inside code, where `@app.route` is a
//     decorator rather than a reference;
//   - an accept is applied by the caller through the browser's editing stack,
//     so it is handed back as a range and its replacement, never a new draft;
//   - the empty query is a screen of its own: the feature the composer writes
//     inside and its work, recent work, then one row per kind that narrows the
//     search to that kind.
import { computed, ref, toValue } from 'vue'
import api from '../api'
import { KINDS, canonicalKind, codeRanges, isValidKey } from '../utils/refs.js'

const DEBOUNCE_MS = 180
const QUERY_MAX = 64

// An `@` right after one of these belongs to a word or an address.
const WORD_CHAR = /[A-Za-z0-9._%+-]/
// What a query cannot hold. Reaching one means the caret left the token.
const OUTSIDE_QUERY = /[\s@[\]()]/
const LINE_BREAK = /[\r\n]/
const KIND_PREFIX = /^([A-Za-z]+):(.*)$/s
const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta'])

export const KIND_LABELS = {
  feature: 'Feature', task: 'Task', defect: 'Defect',
  session: 'Session', file: 'File', memory: 'Memory',
}

const URL_ROUTES = [
  ['feature', /^\/features\/([^/]+)\/?$/],
  ['task', /^\/tasks\/([^/]+)\/?$/],
  ['defect', /^\/defects\/([^/]+)\/?$/],
  ['session', /^\/trace\/sessions\/([^/]+)\/?$/],
]

// A fence is read with the grammar's own rules, so an unclosed one runs
// to the caret. An inline span being typed has no closing backtick yet, which
// is why an odd count of backticks on the line counts as inside.
function inCode(text, at) {
  let lineStart = at
  while (lineStart > 0 && !LINE_BREAK.test(text[lineStart - 1])) lineStart -= 1
  let ticks = 0
  for (let i = lineStart; i < at; i += 1) if (text[i] === '`') ticks += 1
  if (ticks % 2 === 1) return true
  return codeRanges(text.slice(0, at + 1)).some(([lo, hi]) => lo <= at && at < hi)
}

/** The `@<query>` ending at the caret, as `{ start, end, query }`; null when there is none. */
export function refToken(text, caret) {
  const value = text ?? ''
  const pos = Math.min(Math.max(caret ?? 0, 0), value.length)
  const floor = Math.max(0, pos - QUERY_MAX - 1)
  for (let i = pos - 1; i >= floor; i -= 1) {
    const ch = value[i]
    if (ch === '@') {
      if (i > 0 && WORD_CHAR.test(value[i - 1])) return null
      if (inCode(value, i)) return null
      return { start: i, end: pos, query: value.slice(i + 1, pos) }
    }
    if (OUTSIDE_QUERY.test(ch)) return null
  }
  return null
}

/** The reference a pasted URL of this app names, as `kind:key`; null for any other text. */
export function refForUrl(text, origin) {
  const raw = (text ?? '').trim()
  if (!raw || /\s/.test(raw)) return null
  let url
  try {
    url = new URL(raw)
  } catch {
    return null
  }
  if (url.origin !== origin) return null
  for (const [kind, route] of URL_ROUTES) {
    const key = route.exec(url.pathname)?.[1]
    if (key && isValidKey(kind, key)) return `${kind}:${key}`
  }
  return null
}

function projectId(value) {
  const raw = String(toValue(value) ?? '')
  return /^\d+$/.test(raw) ? Number(raw) : null
}

// The text a drill keeps: `task:nav` drilled into Defect searches `nav`.
function searchText(query) {
  const match = KIND_PREFIX.exec(query)
  return match && canonicalKind(match[1].toLowerCase()) ? match[2] : query
}

function byKind(a, b) {
  return KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind)
}

// What the menu draws for one answer. Rows carry their position in keyboard
// order, which skips headers, hints and notes.
function screen(found, canReadSessions) {
  let index = 0
  const itemRow = (item) => ({ type: 'item', id: item.ref, kind: item.kind, item, index: index++ })
  const sections = []
  if (found.failed) {
    // Nothing to list; the note says why.
  } else if (found.query === '') {
    if (found.context.length) {
      sections.push({ key: 'context', label: 'This feature', rows: found.context.map(itemRow) })
    }
    const recent = found.groups.flatMap((group) => group.items)
    if (recent.length) sections.push({ key: 'recent', label: 'Recent', rows: recent.map(itemRow) })
  } else {
    for (const group of [...found.groups].sort(byKind)) {
      sections.push({
        key: group.kind,
        kind: group.kind,
        label: group.hint ? KIND_LABELS[group.kind] : `${KIND_LABELS[group.kind]} · ${group.total}`,
        hint: group.hint || '',
        rows: group.items.map(itemRow),
      })
    }
  }
  if (found.query === '') {
    const kinds = KINDS.filter((kind) => kind !== 'session' || canReadSessions)
    sections.push({
      key: 'kinds',
      label: 'Search one kind',
      rows: kinds.map((kind) => ({ type: 'kind', id: `kind:${kind}`, kind, index: index++ })),
    })
  }
  return sections
}

export function useRefMentions({
  project = null,
  // The feature the composer writes inside: it, its tasks and its defects
  // lead every list, and lend it their project when none is given.
  feature = null,
  // A non-admin is offered no Session group, so no Session row either.
  canReadSessions = () => api.getStoredUser()?.role === 'admin',
} = {}) {
  const open = ref(false)
  const query = ref('')
  // The answer on screen: `{ query, groups, failed }`. It stays drawn while a
  // newer query is in flight, which is what `stale` is about.
  const answer = ref(null)
  const activeIndex = ref(0)
  const pending = ref(false)
  // Bumped when an armed accept is ready. The caller owns the textarea, so it
  // watches this and applies the accept itself.
  const armedResolved = ref(0)

  let seq = 0
  let timer = null
  let token = null
  // An Enter/Tab pressed while the list was stale, kept as { query, start }:
  // when that query's rows land on that token, the highlighted row is
  // accepted. Any other keystroke, a close, or an empty answer clears it.
  let armed = null

  const selected = computed(() => projectId(project))
  const context = computed(() => projectId(feature))
  const stale = computed(() => answer.value?.query !== query.value)
  const loading = computed(() => pending.value || stale.value)
  const sections = computed(() => (answer.value ? screen(answer.value, canReadSessions()) : []))
  const rows = computed(() => sections.value.flatMap((section) => section.rows))
  const note = computed(() => {
    const found = answer.value
    if (!found) return { kind: 'busy', text: 'Searching…' }
    if (found.failed) return { kind: 'failed', text: 'Search failed — keep typing to retry' }
    if (found.query !== '' && !found.groups.length) {
      return { kind: 'empty', text: `No matches for "${found.query}"` }
    }
    return null
  })
  const tokenStart = computed(() => (open.value && token ? token.start : null))

  function clampIndex() {
    const n = rows.value.length
    if (activeIndex.value > n - 1) activeIndex.value = Math.max(0, n - 1)
    if (activeIndex.value < 0) activeIndex.value = 0
  }

  async function run(q, scope, within) {
    const mine = ++seq
    let found
    try {
      const params = new URLSearchParams()
      if (q) params.set('q', q)
      if (scope !== null) params.set('project_id', String(scope))
      if (within !== null) params.set('feature_id', String(within))
      const res = await api.get(`/refs?${params}`)
      found = {
        query: q,
        groups: Array.isArray(res?.groups) ? res.groups : [],
        context: Array.isArray(res?.context) ? res.context : [],
        failed: false,
      }
    } catch {
      found = { query: q, groups: [], context: [], failed: true }
    }
    if (mine !== seq) return
    answer.value = found
    pending.value = false
    // A new list starts at its top. An index moved on the stale rows points
    // at a row of this one nobody has seen.
    activeIndex.value = 0
    resolveArmed(q)
  }

  // The accept fires only for the query and token it was armed on, and only
  // when that query produced rows: a reference nobody chose is never written.
  function resolveArmed(q) {
    if (!armed || armed.query !== q) return
    const hit = q === query.value && token?.start === armed.start
    armed = null
    if (hit && rows.value.length > 0) armedResolved.value += 1
  }

  // One request per pause, not per keystroke. `pending` flips at once: the
  // menu is already open, and 180 ms of "no matches" would be a lie. The
  // sequence moves here, not when the request is sent: an answer to an older
  // query that lands inside this debounce is dropped, rather than drawn as
  // current and taken for the answer an armed Enter is waiting on.
  function schedule(q) {
    if (timer) clearTimeout(timer)
    seq++
    pending.value = true
    const scope = selected.value
    const within = context.value
    timer = setTimeout(() => {
      timer = null
      run(q, scope, within)
    }, DEBOUNCE_MS)
  }

  // Only a bare caret can sit in a token: a selection is about to be replaced.
  function sync(text, start, end = start) {
    const tok = start === end ? refToken(text, end) : null
    if (!tok) {
      close()
      return
    }
    const changed = !open.value || tok.query !== query.value || tok.start !== token?.start
    token = tok
    query.value = tok.query
    open.value = true
    if (changed) {
      armed = null
      activeIndex.value = 0
      schedule(tok.query)
    }
    clampIndex()
  }

  function move(delta) {
    const n = rows.value.length
    if (n === 0) return
    activeIndex.value = (activeIndex.value + delta + n) % n
  }

  function setActive(i) {
    activeIndex.value = i
  }

  function close() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    seq++ // orphan any response still in flight
    open.value = false
    pending.value = false
    query.value = ''
    activeIndex.value = 0
    answer.value = null
    token = null
    armed = null
  }

  /**
   * The edit accepting `row` makes, as `{ start, end, text }` over the token;
   * null when nothing may be accepted. A stale list accepts nothing, by key
   * or by click: its rows answer a query the user has typed past.
   *
   * An item becomes `[label](ref)` plus a space. A kind narrows the token to
   * `@<kind>:`, keeping any text already typed.
   */
  function editFor(row) {
    if (!open.value || !token || !row || stale.value) return null
    if (row.type === 'kind') {
      const text = searchText(query.value).slice(0, QUERY_MAX - row.kind.length - 1)
      return { start: token.start, end: token.end, text: `@${row.kind}:${text}` }
    }
    return { start: token.start, end: token.end, text: `[${row.item.label}](${row.item.ref}) ` }
  }

  // Returns `{ handled, row }`: a handled key is the caller's to consume,
  // and `row` is the one it accepts. Cmd/Ctrl+Enter and Shift+Enter fall
  // through, so a host's send shortcut still works with the menu open.
  function handleKeydown(e) {
    if (!open.value) return { handled: false }
    const { key } = e
    const modified = e.metaKey || e.ctrlKey || e.altKey || e.shiftKey
    if (key !== 'Enter' && key !== 'Tab' && !MODIFIER_KEYS.has(key)) armed = null
    if ((key === 'ArrowDown' || key === 'ArrowUp') && !modified) {
      move(key === 'ArrowDown' ? 1 : -1)
      return { handled: true }
    }
    if (key === 'Escape') {
      close()
      return { handled: true }
    }
    if ((key !== 'Enter' && key !== 'Tab') || modified) return { handled: false }
    if (stale.value && query.value === '') {
      // Nothing typed names a row yet. The Recent screen is never empty, so
      // arming here would write whichever reference lands on top — one the
      // operator never saw. The key is the textarea's: a newline, or focus.
      if (key === 'Enter') close()
      return { handled: false }
    }
    if (stale.value) {
      // Consumed and armed rather than dropped: at real latency every first
      // Enter lands in this window, and dropping it would cost two presses.
      armed = token ? { query: query.value, start: token.start } : null
      return { handled: true }
    }
    const row = rows.value[activeIndex.value]
    if (row) return { handled: true, row }
    if (key === 'Enter') close()
    return { handled: false }
  }

  return {
    open, query, sections, rows, activeIndex, stale, loading, note, selected,
    context, tokenStart, armedResolved,
    sync, move, setActive, close, editFor, handleKeydown,
  }
}
