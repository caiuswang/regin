import { ref, computed, watch } from 'vue'
import api from '../api'
import {
  KIND_META, KIND_ORDER, MIN_QUERY_CHARS, SCOPE_KINDS, SPAN_ROW_CAP,
  addressOf, countText, facetsFor, groupCap, idPrefixOf, kindFacetLabel,
  kindNoun, rowDoc, rowKey, searchLocalSpans,
} from '../utils/searchDocs.js'
import { listDrillId, usePaletteDrill } from './usePaletteDrill.js'
import { chainSection, deskSections, usePaletteHome } from './usePaletteHome.js'
import { useTraceSearchSource } from './useTraceSearchSource.js'

const FACET_KEY = 'regin.search.facet'
const SCOPE_KEY = 'regin.search.scope'
const RECENTS_KEY = 'regin.search.recents'
const MAX_RECENTS = 6
// Long enough that a fast typist issues one request per word, short enough
// that a pause feels answered. Only the REMOTE leg waits — the local span
// pass runs on every keystroke.
const DEBOUNCE_MS = 140
const DRILL_TAIL = 'drill:tail'
// Kinds the empty query can list without a search: picking one pages
// `/api/palette/list/<kind>`. Sessions are not here — their empty query is
// the roster.
const LISTED_KINDS = new Set(['feature', 'task', 'defect'])
const LIST_LABEL = { needs_me: 'Needs me', active_now: 'Active now', active_features: 'Active features' }
const LIST_NOUN = { needs_me: 'waiting', active_now: 'active', active_features: 'features' }

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

const toRow = doc => ({ type: 'doc', key: rowKey(doc), doc, hits: doc.bodyHits || 0 })
const moreEntry = (kind, total) => ({ type: 'more', key: `more:${kind}`, kind, total })
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

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
// One answer per kind, each its own request: a slow corpus (ripgrep over the
// touched files) or a failing one no longer holds every other group back, and
// its header and chip can say `…` or `!` on their own.
// kind → { status: 'pending' | 'ok' | 'failed', total, items }
const kindStates = ref({})
const resolved = ref({ status: 'idle', data: null })
// The kind an ADDRESS was drilled into. Held apart from the stored facet: a
// kind left picked by an earlier search must not turn `T999` into a text
// search for 999 — an address reaches a Drill only by the reader asking.
const addressDrill = ref(null)
// The Home group whose "Show all" was opened — a list with no query behind it.
const homeList = ref(null)
const recents = ref(readRecents())
// Set when ⌃S asks for a scope that has no referent (R8: a no-op with a
// notice). Cleared by the next thing the reader does to the query or scope,
// so it always describes the keystroke just made.
const scopeNotice = ref('')
// The entry the keyboard is on, held by key rather than by index: new spans
// stream into a live session and each kind's answer lands out of band, so an
// index would silently point at a different row than the one highlighted.
const activeId = ref(null)
// The element focus must return to on close.
let restoreFocusTo = null
let debounceTimer = null
let drillTimer = null
// Bumped by every new question; an answer to an older one is dropped.
let generation = 0

export function useFullTextSearch() {
  const { source } = useTraceSearchSource()
  const { drill, reset: resetDrill, loadPage } = usePaletteDrill(toRow)
  const { home, load: loadHome, reset: resetHome } = usePaletteHome()

  // 'This session' is only offerable while a trace is actually open; from the
  // inbox or the dashboard there is no session to be inside of. Reading it as
  // global (rather than rewriting the stored value) is what keeps the
  // preference intact across a detour.
  const sessionAvailable = computed(() => !!source.value?.traceId)

  const trimmed = computed(() => query.value.trim())
  // The trace-id prefix a `#` query addresses, or null when it is anything
  // else. No corpus is asked for, and the Sessions body renders the resolved
  // rows instead of the roster.
  const idPrefix = computed(() => idPrefixOf(trimmed.value))
  // F146 / T… / D… / a bare number: resolved by id, not searched as text.
  const address = computed(() => (idPrefix.value === null ? addressOf(trimmed.value) : null))

  // The sigil forces the same two axes ⌘J does — Sessions, everywhere — but
  // DERIVES them from the query rather than pinning an override. A pin would
  // have to be un-pinned, and the query survives a close: reopening on a `#`
  // whose forcing had been dropped would search a corpus for a uuid and show
  // an empty result list where the addressed session should be.
  const addressing = computed(() => idPrefix.value !== null)
  const activeScope = computed(() => {
    if (addressing.value || address.value) return 'global'
    return sessionAvailable.value ? (scopeOverride.value ?? scope.value) : 'global'
  })
  const activeKinds = computed(() => SCOPE_KINDS[activeScope.value])
  const facets = computed(() => facetsFor(activeScope.value))
  // A facet the current scope cannot show reads as All rather than as an
  // empty list — the stored label stays untouched for when its scope returns.
  const activeFacet = computed(() => {
    if (addressing.value) return 'Sessions'
    if (address.value) return kindFacetLabel(addressDrill.value)
    const want = facetOverride.value ?? facet.value
    return facets.value.some(f => f.label === want) ? want : 'All'
  })
  const activeKind = computed(() =>
    facets.value.find(f => f.label === activeFacet.value)?.kind ?? null)

  const held = computed(() => !address.value && trimmed.value.length < MIN_QUERY_CHARS)

  // The Sessions facet with nothing typed IS the switcher; type into it and it
  // goes back to being the cross-session digest search it already was.
  const switcherMode = computed(() =>
    activeFacet.value === 'Sessions'
    && (held.value || idPrefix.value !== null))
  const mode = computed(() => {
    if (addressing.value) return 'hash'
    if (address.value) return 'address'
    return held.value ? 'held' : 'search'
  })

  // A kind picked out of the list IS its Drill: every match, paged. Spans are
  // the exception — the in-session span group is a fixed window the server
  // does not page, so picking it only filters the list down to that group.
  const drillKind = computed(() => {
    const kind = activeKind.value
    if (!kind || kind === 'span') return null
    return (mode.value === 'search' || mode.value === 'address') ? kind : null
  })
  // The empty query's Drill: a Home group's "Show all", or a kind picked
  // before typing. Without it the pick changed nothing and every kind
  // showed the same desk.
  const browseKey = computed(() => {
    if (mode.value !== 'held' || trimmed.value || switcherMode.value) return null
    if (homeList.value) return homeList.value
    return LISTED_KINDS.has(activeKind.value) ? activeKind.value : null
  })
  const drillQuery = computed(() => address.value?.needle ?? trimmed.value)
  const drillId = computed(() => {
    if (browseKey.value) return listDrillId(browseKey.value)
    return drillKind.value
      ? JSON.stringify([drillKind.value, drillQuery.value, activeScope.value,
        source.value?.traceId || ''])
      : ''
  })

  const localSpans = computed(() => {
    if (mode.value !== 'search' || !source.value) return []
    return searchLocalSpans(source.value.spans() || [], trimmed.value)
  })

  // The span group: local rows answer instantly, then the server's
  // authoritative set supersedes them BY span_id. The server searched the
  // whole session; the client only searched what is paginated in, so any id
  // the server returned wins and the leftover local rows are appended for
  // the spans it capped off.
  const spanRows = computed(() => {
    const st = kindStates.value.span
    const remote = st?.status === 'ok' ? st : null
    const remoteItems = (remote?.items || []).map(toRow)
    const seen = new Set(remoteItems.map(r => r.key))
    const local = localSpans.value.map(r => ({ ...toRow(r.doc), hits: r.hits }))
    const extras = local.filter(r => !seen.has(r.key))
    const rows = remote ? [...remoteItems, ...extras] : local
    return { rows, total: (remote?.total ?? rows.length) }
  })

  const statusOf = kind => kindStates.value[kind]?.status ?? 'idle'

  function groupFor(kind) {
    const status = statusOf(kind)
    const base = {
      kind, status, pending: status === 'pending', failed: status === 'failed',
    }
    if (kind === 'span') {
      const { rows, total } = spanRows.value
      // The span group reports a remote failure even though its local rows
      // still render: the client model is shallow and paginated, so
      // local-only results are a WINDOW of the session, not the session.
      return { ...base, rows: rows.slice(0, SPAN_ROW_CAP), total,
        pending: base.pending && !rows.length, more: null }
    }
    const st = kindStates.value[kind]
    const rows = (st?.items || []).slice(0, groupCap(kind)).map(toRow)
    const total = st?.total ?? 0
    // R2: a group that holds back rows ends in a row that reaches them.
    const more = status === 'ok' && total > rows.length ? moreEntry(kind, total) : null
    return { ...base, rows, total, more }
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

  const askedKinds = computed(() => (mode.value === 'search' ? activeKinds.value : []))
  const pendingKinds = computed(() => askedKinds.value.filter(k => statusOf(k) === 'pending'))
  const failedKinds = computed(() => askedKinds.value.filter(k => statusOf(k) === 'failed'))
  // Every kind failed: nothing was searched at all, which is a different
  // statement from "these two kinds failed" and gets its own state.
  const offline = computed(() => askedKinds.value.length > 0
    && failedKinds.value.length === askedKinds.value.length)

  function addressState(kind) {
    const { status, data } = resolved.value
    if (status !== 'ok') return { status }
    const hits = (data.hits || []).filter(r => !kind || r.kind === kind).length
    const text = (data.text || []).filter(g => !kind || g.kind === kind)
      .reduce((n, g) => n + (g.total || 0), 0)
    return { status, total: hits + text }
  }

  // The empty query asked no kind anything; its chips read each kind's
  // total from the desk instead.
  function heldState(kind) {
    const { status, data } = home.value
    const total = data?.totals?.[kind]
    return status === 'ok' && total != null ? { status, total } : { status: 'idle' }
  }

  function kindState(kind) {
    if (mode.value === 'address') return addressState(kind)
    if (mode.value === 'held') return heldState(kind)
    if (kind === 'span') return { status: statusOf('span'), total: spanRows.value.total }
    return { status: statusOf(kind), total: kindStates.value[kind]?.total ?? 0 }
  }

  function allText() {
    if (offline.value) return '—'
    if (mode.value === 'address') return countText(addressState(null))
    const unsettled = pendingKinds.value.length + failedKinds.value.length
    return `${counts.value.All}${unsettled ? '+' : ''}`
  }

  // The rail's chips. A kind that answered 0 is disabled, and ⇥ skips it.
  const facetView = computed(() => facets.value.map((f) => {
    if (!f.kind) return { ...f, text: mode.value === 'held' ? '' : allText(), disabled: false }
    const st = kindState(f.kind)
    return {
      ...f,
      text: st.status === 'idle' ? '' : countText(st, offline.value),
      disabled: offline.value || (st.status === 'ok' && !st.total),
    }
  }))

  const groups = computed(() => {
    if (mode.value !== 'search') return []
    return KIND_ORDER
      .filter(k => activeKinds.value.includes(k))
      .filter(k => !activeKind.value || k === activeKind.value)
      .map(groupFor)
      .filter(g => g.rows.length || g.pending || g.failed)
  })

  const drillView = computed(() => {
    const listed = browseKey.value
    const kind = drillKind.value || listed
    if (!kind) return null
    const d = drill.value
    const st = kindState(kind)
    // The header quotes the kind's group total — the same number its chip
    // says — and falls back to the Drill's own once only the Drill has asked.
    let total = st.status === 'ok' && mode.value === 'search' ? st.total : d.total
    // Before its first page a listed Drill quotes the count the desk already
    // showed for it, not the empty page's 0.
    if (listed && d.status !== 'ok') total = home.value.data?.[listed]?.total ?? st.total ?? total
    let tail = null
    if (d.status === 'failed') tail = { type: 'retry', key: DRILL_TAIL }
    else if (d.items.length < d.total) {
      tail = { type: 'load', key: DRILL_TAIL, left: d.total - d.items.length,
        loading: d.status === 'loading' }
    }
    return {
      kind, label: LIST_LABEL[kind] || KIND_META[kind]?.label || kind, total,
      query: listed ? '' : drillQuery.value, back: listed ? '← Home' : '← All kinds',
      rows: d.items, tail, status: d.status,
      done: d.status === 'ok' && d.items.length >= d.total,
    }
  })

  // The empty query (R1 Home): the desk Everywhere, the chain inside a
  // session. The Sessions facet keeps its roster, which IS its empty query.
  const homeView = computed(() => {
    if (mode.value !== 'held' || trimmed.value || switcherMode.value || browseKey.value) return null
    const { status, data } = home.value
    if (activeScope.value === 'session') {
      const traceId = source.value?.traceId || ''
      return { chain: true, status, traceId, sections: status === 'ok' ? [chainSection(data)] : [] }
    }
    const sections = status === 'ok' ? deskSections(data, source.value?.traceId) : []
    return { chain: false, status, sections, waiting: data?.needs_me?.total ?? 0 }
  })

  const addressView = computed(() => {
    if (mode.value !== 'address' || drillKind.value) return null
    const { status, data } = resolved.value
    const { kind, needle } = address.value
    const text = (data?.text || []).map((g) => {
      const rows = (g.items || []).map(r => toRow(rowDoc(r)))
      return {
        kind: g.kind, label: `${KIND_META[g.kind]?.label || g.kind} · text`,
        total: g.total, rows,
        more: g.total > rows.length ? moreEntry(g.kind, g.total) : null,
      }
    })
    return {
      status, needle, number: kind === 'number',
      hits: (data?.hits || []).map(r => toRow(rowDoc(r))),
      text, miss: data?.miss || null,
    }
  })

  const entries = computed(() => {
    const dv = drillView.value
    if (dv) return dv.tail ? [...dv.rows, dv.tail] : dv.rows
    const av = addressView.value
    const hv = homeView.value
    let sections = groups.value
    if (av) sections = [{ rows: av.hits }, ...av.text]
    else if (hv) sections = hv.sections
    return sections.flatMap(s => (s.more ? [...s.rows, s.more] : s.rows))
  })

  // Nothing is preselected while an answer is still on its way: a group
  // landing above the first row would otherwise move the highlight out from
  // under a reader who has not touched a key.
  const settling = computed(() => {
    if (drillView.value) {
      const d = drill.value
      return d.status === 'pending' || (d.status === 'loading' && !d.items.length)
    }
    if (mode.value === 'address') return resolved.value.status === 'pending'
    if (homeView.value) return homeView.value.status === 'pending'
    return pendingKinds.value.length > 0
  })

  const activeEntry = computed(() =>
    entries.value.find(e => e.key === activeId.value) || null)
  const activeRow = computed(() =>
    (activeEntry.value?.type === 'doc' ? activeEntry.value : null))

  // Selection is re-pinned only when the entry it named has left the list;
  // holding it by key is what lets a streaming session grow underneath the
  // reader without moving the highlight.
  watch([entries, settling], ([list, busy]) => {
    if (list.some(e => e.key === activeId.value)) return
    activeId.value = busy ? null : (list.find(e => e.type === 'doc')?.key ?? null)
  })

  const noMatches = computed(() => mode.value === 'search' && !drillKind.value
    && !settling.value && !offline.value && !entries.value.length)

  const headerCount = computed(() => {
    const hv = homeView.value
    if (hv?.chain) return 'this session'
    if (hv?.status === 'ok') return hv.waiting ? `${hv.waiting} waiting on you` : 'nothing waiting'
    const dv = drillView.value
    if (dv) return `${dv.total} ${LIST_NOUN[dv.kind] || kindNoun(dv.kind, dv.total)}`
    if (mode.value === 'held') return 'Everything indexed'
    if (mode.value === 'address') {
      const st = addressState(null)
      if (st.status === 'pending') return 'resolving…'
      if (st.status === 'failed') return 'offline'
      return st.total ? plural(st.total, 'result') : '0 resolved'
    }
    if (offline.value) return 'offline'
    if (activeKind.value) {
      const n = counts.value[activeKind.value] ?? 0
      return `${n} ${kindNoun(activeKind.value, n)}`
    }
    const out = [`${allText()} ${counts.value.All === 1 ? 'result' : 'results'}`]
    if (pendingKinds.value.length) out.push(`${plural(pendingKinds.value.length, 'kind')} loading`)
    if (failedKinds.value.length) out.push(`${plural(failedKinds.value.length, 'kind')} failed`)
    return out.join(' · ')
  })

  function setKind(kind, gen, next) {
    if (gen !== generation) return
    kindStates.value = { ...kindStates.value, [kind]: next }
  }

  async function fetchKind(kind, q, gen) {
    const params = new URLSearchParams({
      q, kinds: kind, limit: String(groupCap(kind)), scope: activeScope.value,
    })
    // The open trace is sent under BOTH scopes; `scope` is what says how to
    // read it — the corpus to search, or the session to leave out of a
    // cross-session answer.
    if (source.value?.traceId) params.set('session', source.value.traceId)
    try {
      const data = await api.get(`/search?${params}`)
      const group = (data.groups || []).find(g => g.kind === kind)
      const failed = !group || (data.failed || []).includes(kind)
      setKind(kind, gen, failed
        ? { status: 'failed', items: [], total: 0 }
        : { status: 'ok', items: group.items || [], total: group.total ?? 0 })
    } catch {
      setKind(kind, gen, { status: 'failed', items: [], total: 0 })
    }
  }

  async function runResolve(q, gen) {
    try {
      const data = await api.get(`/palette/resolve?ref=${encodeURIComponent(q)}`)
      if (gen === generation) resolved.value = { status: 'ok', data }
    } catch {
      if (gen === generation) resolved.value = { status: 'failed', data: null }
    }
  }

  // Each kind goes pending — and empty — the moment the question changes, not
  // when its request leaves: the rows on screen answer the previous question,
  // and leaving them selectable lets ↵ open a hit for a query no longer typed.
  function markPending(kinds) {
    kindStates.value = Object.fromEntries(kinds.map(k => [k, {
      items: [], total: 0, status: 'pending',
    }]))
  }

  function scheduleRemote() {
    if (debounceTimer) clearTimeout(debounceTimer)
    generation += 1
    const gen = generation
    const q = trimmed.value
    resolved.value = { status: 'idle', data: null }
    // An address is not a query. Letting the fan-out run would spend ripgrep
    // and a span scan on a uuid nothing indexes, and its answer would land
    // under a list that is not showing search results.
    if (mode.value === 'held' || mode.value === 'hash') {
      kindStates.value = {}
      syncHome()
    } else if (mode.value === 'address') {
      kindStates.value = {}
      resetHome()
      resolved.value = { status: 'pending', data: null }
      debounceTimer = setTimeout(() => runResolve(q, gen), DEBOUNCE_MS)
    } else {
      const kinds = activeKinds.value
      resetHome()
      markPending(kinds)
      debounceTimer = setTimeout(() => kinds.forEach(k => fetchKind(k, q, gen)), DEBOUNCE_MS)
    }
    syncDrill()
  }

  // Home is asked on every open and every return to the empty query, so the
  // desk is as fresh as the moment it is looked at; a held query that is not
  // empty asks nothing. The same question already pending or answered is not
  // re-asked: a facet pick leaves the desk as it was, and the palette's two
  // composable instances both reach here on one scope change.
  function syncHome() {
    if (!open.value || trimmed.value || switcherMode.value) { resetHome(); return }
    const session = activeScope.value === 'session' ? source.value?.traceId || '' : ''
    const { session: asked, status } = home.value
    if (asked === session && (status === 'pending' || status === 'ok')) return
    loadHome(session)
  }

  // The Drill re-asks only when its question changed. Both callers of this
  // composable reach here; the id comparison is what keeps that to one fetch.
  function syncDrill() {
    const id = open.value ? drillId.value : ''
    if (id === drill.value.id) return
    if (drillTimer) clearTimeout(drillTimer)
    resetDrill(id)
    if (id) drillTimer = setTimeout(() => loadPage(), DEBOUNCE_MS)
  }
  watch(drillId, syncDrill)

  // Landing on the load row loads it (R2), and the cursor moves onto the
  // first row the page brought in, so ↓ alone walks every match.
  function loadMore() {
    return loadPage((fresh) => {
      if (fresh.length && activeId.value === DRILL_TAIL) activeId.value = fresh[0].key
    })
  }

  function retryKind(kind) {
    if (mode.value !== 'search') { scheduleRemote(); return }
    kindStates.value = { ...kindStates.value, [kind]: { items: [], total: 0, status: 'pending' } }
    fetchKind(kind, trimmed.value, generation)
  }

  function retry() {
    if (mode.value !== 'hash') scheduleRemote()
  }

  function setQuery(next) {
    query.value = next
    addressDrill.value = null
    homeList.value = null
    scopeNotice.value = ''
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
    if (address.value) {
      addressDrill.value = facets.value.find(f => f.label === label)?.kind ?? null
      return
    }
    facetOverride.value = null
    homeList.value = null
    facet.value = label
    writeStored(FACET_KEY, label)
    if (open.value && mode.value === 'held') syncHome()
  }

  function cycleFacet(delta) {
    const list = facetView.value
    const n = list.length
    const at = list.findIndex(f => f.label === activeFacet.value)
    for (let step = 1; step < n; step += 1) {
      const next = list[(((at + delta * step) % n) + n) % n]
      if (!next.disabled) { setFacet(next.label); return }
    }
  }

  const drillForced = computed(() => addressing.value
    || (facetOverride.value !== null && !address.value))
  // Esc and ⌫ step out of a kind the reader picked; a kind ⌘J or `#` forced
  // is not a layer they stepped into.
  // With no query the Drill is a listed kind or a Home group's "Show all";
  // esc steps back to the desk.
  const inDrill = computed(() => browseKey.value !== null || (activeKind.value !== null
    && !drillForced.value && (mode.value === 'search' || mode.value === 'address')))

  function drillInto(kind) {
    activeId.value = null
    setFacet(kindFacetLabel(kind))
  }

  function leaveDrill() {
    activeId.value = null
    setFacet('All')
  }

  // A Home group's "Show all": every entry behind it, paged.
  function showHomeList(key) {
    activeId.value = null
    homeList.value = key
  }

  function setScope(next) {
    if (addressing.value) return
    if (next === 'session' && !sessionAvailable.value) return
    scopeNotice.value = ''
    scopeOverride.value = null
    scope.value = next
    writeStored(SCOPE_KEY, next)
  }

  function toggleScope() {
    const next = activeScope.value === 'session' ? 'global' : 'session'
    if (next === 'session' && !sessionAvailable.value && !addressing.value) {
      scopeNotice.value = 'No session is open, so there is no “This session” to search. '
        + 'Open a trace to scope to it.'
      return
    }
    setScope(next)
  }

  // Scope decides which corpora were even asked for, so the previous answer is
  // not a stale view of the new one — it is an answer to a different question.
  // Dropping it is what makes the group headers pend rather than assert.
  watch(activeScope, () => {
    kindStates.value = {}
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

  // ⌘K over the open roster is the way back to search: the roster has no rail
  // to pick a kind from, so the chord drops ⌘J's forcing instead of no-oping.
  function openPalette() {
    if (open.value) {
      scopeOverride.value = null
      facetOverride.value = null
    } else {
      restoreFocusTo = document.activeElement
      open.value = true
    }
    scheduleRemote()
  }

  // ⌘J is not a second surface: it is ⌘K with the facet forced and the query
  // empty, the same relationship the old ⌘F had to Spans — minus the leak.
  function openSwitcher() {
    showRoster()
    if (!open.value) restoreFocusTo = document.activeElement
    open.value = true
    scheduleRemote()
  }

  function showRoster() {
    scopeOverride.value = 'global'
    facetOverride.value = 'Sessions'
    setQuery('')
  }

  // The ⌘J roster proper: the switcher reached by the chord, not by a reader
  // who picked the Sessions kind in ⌘K and still wants the rail they clicked.
  const rosterMode = computed(() =>
    switcherMode.value && idPrefix.value === null && facetOverride.value === 'Sessions')

  // The query survives close deliberately — reopening with ⌘K is how a user
  // walks back into the same result set after reading one hit.
  function closePalette() {
    open.value = false
    scopeOverride.value = null
    facetOverride.value = null
    scopeNotice.value = ''
    homeList.value = null
    resetHome()
    if (debounceTimer) clearTimeout(debounceTimer)
    if (drillTimer) clearTimeout(drillTimer)
    generation += 1
    // A Drill whose first page was still debouncing would otherwise reopen
    // as `pending` with the same id, which `syncDrill` reads as already asked.
    resetDrill('')
    const el = restoreFocusTo
    restoreFocusTo = null
    if (el && typeof el.focus === 'function' && document.contains(el)) el.focus()
  }

  function moveActive(delta) {
    const list = entries.value
    if (!list.length) return
    const at = list.findIndex(e => e.key === activeId.value)
    // A page on its way is where ↓ is going: wrapping to the top from here
    // would leave the reader at row 1 when the rows they asked for land.
    if (delta > 0 && list[at]?.type === 'load' && list[at].loading) return
    let next = (at + delta + list.length) % list.length
    if (at < 0) next = delta > 0 ? 0 : list.length - 1
    const entry = list[next]
    activeId.value = entry.key
    if (entry.type === 'load' && !entry.loading) loadMore()
  }

  return {
    open, query, facet, recents, activeId, activeRow, activeEntry, groups,
    counts, entries, held, trimmed, idPrefix, mode, offline, settling,
    activeScope, activeFacet, activeKinds, facets, facetView, sessionAvailable,
    switcherMode, rosterMode, drillView, addressView, homeView, noMatches, headerCount, inDrill,
    scopeNotice, openPalette, openSwitcher, closePalette, setQuery, setFacet, cycleFacet,
    moveActive, drillInto, leaveDrill, showHomeList, loadMore, retryKind,
    setScope, toggleScope, rememberQuery, retry, source,
  }
}
