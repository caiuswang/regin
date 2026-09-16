import { ref, computed, watch } from 'vue'
import api from '../api'
import {
  KIND_ORDER, MIN_QUERY_CHARS, OTHER_ROW_CAP, SCOPE_KINDS,
  SPAN_ROW_CAP, facetsFor, idPrefixOf, searchLocalSpans,
} from '../utils/searchDocs.js'
import { useTraceSearchSource } from './useTraceSearchSource.js'

const FACET_KEY = 'regin.search.facet'
const SCOPE_KEY = 'regin.search.scope'
const RECENTS_KEY = 'regin.search.recents'
const MAX_RECENTS = 6
// Long enough that a fast typist issues one request per word, short enough
// that a pause feels answered. Only the REMOTE leg waits — the local span
// pass runs on every keystroke.
const DEBOUNCE_MS = 140

function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key)
    return raw == null ? fallback : raw
  } catch { return fallback }
}

function writeStored(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* private mode */ }
}

function readRecents() {
  try { return JSON.parse(readStored(RECENTS_KEY, '[]')) || [] } catch { return [] }
}

const open = ref(false)
const query = ref('')
const facet = ref(readStored(FACET_KEY, 'All'))
// Where to look, independent of which kind to show. Persisted, but never
// overwritten by the palette deciding it cannot honour it — a user who works
// mostly inside sessions should not lose that preference by opening the
// palette once from the inbox.
const scope = ref(readStored(SCOPE_KEY, 'session'))
// ⌘J forces BOTH axes: the Sessions facet, and the global scope that is the
// only one able to offer it. Forcing them through the stored values would
// repeat the exact failure ⌘F had — a pin that outlives the open it was meant
// for, so a later ⌘K silently opens somewhere the reader never chose. Both
// overrides are in-memory, never written, and dropped on close or the moment
// the reader picks a scope or a facet themselves.
const scopeOverride = ref(null)
const facetOverride = ref(null)
const remoteGroups = ref([])
const remoteFailed = ref([])
const remotePending = ref(false)
const recents = ref(readRecents())
// The row the keyboard is on, held by document id rather than by index: new
// spans stream into a live session and the remote answer lands out of band,
// so an index would silently point at a different row than the one the user
// highlighted.
const activeId = ref(null)
// The element focus must return to on close.
let restoreFocusTo = null
let debounceTimer = null
// Every request carries the query it was issued for; a response whose token
// no longer matches is a straggler from an earlier keystroke and is dropped.
let inFlightToken = ''

export function useFullTextSearch() {
  const { source } = useTraceSearchSource()

  // 'This session' is only offerable while a trace is actually open; from the
  // inbox or the dashboard there is no session to be inside of. Reading it as
  // global (rather than rewriting the stored value) is what keeps the
  // preference intact across a detour.
  const sessionAvailable = computed(() => !!source.value?.traceId)

  const trimmed = computed(() => query.value.trim())
  // The trace-id prefix the query addresses, or null when it is an ordinary
  // search. Everything downstream branches on this one value: no corpus is
  // asked for, and the Sessions body renders the resolved rows instead of the
  // roster.
  const idPrefix = computed(() => idPrefixOf(trimmed.value))

  // The sigil forces the same two axes ⌘J does — Sessions, everywhere — but
  // DERIVES them from the query rather than pinning an override. A pin would
  // have to be un-pinned, and the query survives a close: reopening on a `#`
  // whose forcing had been dropped would search a corpus for a uuid and show
  // an empty result list where the addressed session should be.
  const addressing = computed(() => idPrefix.value !== null)
  const activeScope = computed(() => {
    if (addressing.value) return 'global'
    return sessionAvailable.value ? (scopeOverride.value ?? scope.value) : 'global'
  })
  const activeKinds = computed(() => SCOPE_KINDS[activeScope.value])
  const facets = computed(() => facetsFor(activeScope.value))
  // A facet the current scope cannot show reads as All rather than as an
  // empty list — the stored label stays untouched for when its scope returns.
  const activeFacet = computed(() => {
    if (addressing.value) return 'Sessions'
    const want = facetOverride.value ?? facet.value
    return facets.value.some(f => f.label === want) ? want : 'All'
  })

  const held = computed(() => trimmed.value.length < MIN_QUERY_CHARS)

  const localSpans = computed(() => {
    if (held.value || !source.value) return []
    return searchLocalSpans(source.value.spans() || [], trimmed.value)
  })

  // The span group: local rows answer instantly, then the server's
  // authoritative set supersedes them BY span_id. The server searched the
  // whole session; the client only searched what is paginated in, so any id
  // the server returned wins and the leftover local rows are appended for
  // the spans it capped off.
  const spanRows = computed(() => {
    const remote = remoteGroups.value.find(g => g.kind === 'span')
    const remoteItems = (remote?.items || []).map(doc => ({
      doc, score: 0, hits: doc.bodyHits || 0,
    }))
    const seen = new Set(remoteItems.map(r => r.doc.id))
    const extras = localSpans.value.filter(r => !seen.has(r.doc.id))
    const rows = remote ? [...remoteItems, ...extras] : localSpans.value
    return { rows, total: (remote?.total ?? rows.length) }
  })

  function groupFor(kind) {
    if (kind === 'span') {
      const { rows, total } = spanRows.value
      return {
        kind,
        rows: rows.slice(0, SPAN_ROW_CAP),
        total,
        pending: remotePending.value && !rows.length,
        // The span group reports a remote failure even though its local rows
        // still render: the client model is shallow and paginated, so
        // local-only results are a WINDOW of the session, not the session.
        // Staying silent here would present a partial answer as a complete one.
        failed: remoteFailed.value.includes('span'),
      }
    }
    const remote = remoteGroups.value.find(g => g.kind === kind)
    const rows = (remote?.items || []).map(doc => ({ doc, hits: doc.bodyHits || 0 }))
    return {
      kind,
      rows: rows.slice(0, OTHER_ROW_CAP),
      total: remote?.total ?? 0,
      pending: remotePending.value && !remote,
      failed: remoteFailed.value.includes(kind),
    }
  }

  // Counts are computed BEFORE the facet filter, so a chip always says what
  // it would give you — including 0.
  const counts = computed(() => {
    const out = { All: 0 }
    for (const kind of KIND_ORDER.filter(k => activeKinds.value.includes(k))) {
      const n = groupFor(kind).total
      out[kind] = n
      out.All += n
    }
    return out
  })

  const activeKind = computed(() =>
    facets.value.find(f => f.label === activeFacet.value)?.kind ?? null)

  const groups = computed(() => KIND_ORDER
    .filter(k => activeKinds.value.includes(k))
    .map(groupFor)
    .filter(g => !activeKind.value || g.kind === activeKind.value)
    .filter(g => g.rows.length || g.pending || g.failed))

  const flatRows = computed(() => groups.value.flatMap(g => g.rows))

  const activeRow = computed(() =>
    flatRows.value.find(r => r.doc.id === activeId.value) || flatRows.value[0] || null)

  // Selection is re-pinned only when the row it named has left the list;
  // holding it by id is what lets a streaming session grow underneath the
  // reader without moving the highlight.
  watch(flatRows, (rows) => {
    if (!rows.length) { activeId.value = null; return }
    if (!rows.some(r => r.doc.id === activeId.value)) activeId.value = rows[0].doc.id
  })

  async function runRemote(q) {
    const params = new URLSearchParams({
      q,
      limit: String(OTHER_ROW_CAP),
      kinds: activeKinds.value.join(','),
      scope: activeScope.value,
    })
    // The open trace is sent under BOTH scopes; `scope` is what says how to
    // read it — the corpus to search, or the session to leave out of a
    // cross-session answer.
    if (source.value?.traceId) params.set('session', source.value.traceId)
    // The token is the whole request, not just the query. Scope changes which
    // corpora are asked for without touching `q`, so a query-only token let a
    // slow session-scoped answer land on top of a global one and silently drop
    // the groups the global request had asked for.
    const token = params.toString()
    inFlightToken = token
    remotePending.value = true
    try {
      const data = await api.get(`/search?${token}`)
      if (inFlightToken !== token) return
      remoteGroups.value = data.groups || []
      remoteFailed.value = data.failed || []
    } catch {
      if (inFlightToken !== token) return
      remoteGroups.value = []
      // Only the corpora this scope actually asked for can have failed.
      remoteFailed.value = [...activeKinds.value]
    } finally {
      if (inFlightToken === token) remotePending.value = false
    }
  }

  function scheduleRemote() {
    if (debounceTimer) clearTimeout(debounceTimer)
    // An address is not a query. Letting the fan-out run would spend ripgrep
    // and a span scan on a uuid nothing indexes, and its answer would land
    // under a list that is not showing search results.
    if (held.value || idPrefix.value !== null) {
      inFlightToken = ''
      remoteGroups.value = []
      remoteFailed.value = []
      remotePending.value = false
      return
    }
    const q = trimmed.value
    debounceTimer = setTimeout(() => runRemote(q), DEBOUNCE_MS)
  }

  function retry() {
    if (!held.value && idPrefix.value === null) runRemote(trimmed.value)
  }

  function setQuery(next) {
    query.value = next
    scheduleRemote()
  }

  // Only a facet the reader actually picked is persisted; ⌘J's forcing goes
  // through `facetOverride` instead, so it cannot outlive the open.
  //
  // While `#` is addressing, both axes are forced by the QUERY, so a pick here
  // could not move the bar — and persisting one the reader never saw take
  // effect is how a stored preference silently jumps between sessions.
  function setFacet(label) {
    if (addressing.value) return
    facetOverride.value = null
    facet.value = label
    writeStored(FACET_KEY, label)
  }

  function cycleFacet(delta) {
    const list = facets.value
    const at = list.findIndex(f => f.label === activeFacet.value)
    const next = (at + delta + list.length) % list.length
    setFacet(list[next].label)
  }

  function setScope(next) {
    if (addressing.value) return
    if (next === 'session' && !sessionAvailable.value) return
    scopeOverride.value = null
    scope.value = next
    writeStored(SCOPE_KEY, next)
  }

  function toggleScope() {
    setScope(activeScope.value === 'session' ? 'global' : 'session')
  }

  // Scope decides which corpora were even asked for, so the previous answer is
  // not a stale view of the new one — it is an answer to a different question.
  // Dropping it is what makes the group headers pend rather than assert.
  watch(activeScope, () => {
    remoteGroups.value = []
    remoteFailed.value = []
    // `activeScope` also flips on NAVIGATION, when a trace goes out from under
    // the palette. Re-asking while the palette is shut would fan out over
    // ripgrep and the span table for an answer nobody is looking at; the next
    // `openPalette` schedules it anyway.
    if (open.value) scheduleRemote()
  })

  function rememberQuery(q) {
    if (!q) return
    const next = [q, ...recents.value.filter(r => r !== q)].slice(0, MAX_RECENTS)
    recents.value = next
    writeStored(RECENTS_KEY, JSON.stringify(next))
  }

  function openPalette() {
    restoreFocusTo = document.activeElement
    open.value = true
    scheduleRemote()
  }

  // ⌘J is not a second surface: it is ⌘K with the facet forced and the query
  // empty, the same relationship the old ⌘F had to Spans — minus the leak.
  function openSwitcher() {
    scopeOverride.value = 'global'
    facetOverride.value = 'Sessions'
    setQuery('')
    openPalette()
  }

  // The Sessions facet with nothing typed IS the switcher; type into it and it
  // goes back to being the cross-session digest search it already was.
  const switcherMode = computed(() =>
    activeFacet.value === 'Sessions'
    && (held.value || idPrefix.value !== null))

  // The query survives close deliberately — reopening with ⌘K is how a user
  // walks back into the same result set after reading one hit.
  function closePalette() {
    open.value = false
    scopeOverride.value = null
    facetOverride.value = null
    if (debounceTimer) clearTimeout(debounceTimer)
    inFlightToken = ''
    remotePending.value = false
    const el = restoreFocusTo
    restoreFocusTo = null
    if (el && typeof el.focus === 'function' && document.contains(el)) el.focus()
  }

  function moveActive(delta) {
    const rows = flatRows.value
    if (!rows.length) return
    const at = rows.findIndex(r => r.doc.id === activeId.value)
    const next = (at < 0 ? 0 : (at + delta + rows.length) % rows.length)
    activeId.value = rows[next].doc.id
  }

  return {
    open, query, facet, recents, activeId, activeRow, groups, counts,
    flatRows, held, trimmed, idPrefix, remotePending, remoteFailed,
    activeScope, activeFacet, activeKinds, facets, sessionAvailable,
    switcherMode,
    openPalette, openSwitcher, closePalette, setQuery, setFacet, cycleFacet,
    moveActive,
    setScope, toggleScope, rememberQuery, retry, source,
  }
}
