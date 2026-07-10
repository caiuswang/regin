import { ref, computed, reactive, watch, nextTick } from 'vue'

// Per-agent scope for the desktop trace view's Conversation tab — the
// desktop sibling of useLiveScope. scopeId ⇄ `?agent=` (router.replace, the
// same idiom as the `?span=` deep link), so a scoped view is shareable and
// survives tab switches (the param persists; only the Conversation tab
// applies it). No auto-paging loop here: the roster's start_span_id allows a
// direct deep subtree fetch regardless of the loaded turn-anchor window.
//
// The page header keeps showing MAIN-session truth throughout — this only
// re-projects the conversation spine. Scroll save/restore is owned here
// because enter/exit are the only transitions that need it.
export function useTraceScope(route, router, {
  getAgents, getRoster, ensureSpanSubtreeLoaded, ensureTerminalSpansLoaded,
}) {
  const scopeId = ref(route.query.agent || null)
  const loadingSubtree = ref(false)
  let savedScrollTop = null

  // useLiveAgents-shaped entry for the scope bar / scoped feed; null until
  // the roster lands (or when the id is unknown — see notFound).
  const scopedAgent = computed(() => (scopeId.value
    ? getAgents().find(a => a.agentId === scopeId.value) || null
    : null))

  // Raw roster start_span_id — the deep-fetch anchor. Distinct from the
  // entry's `spanId` (which falls back to a synthetic roster- id).
  const startSpanId = computed(() => {
    if (!scopeId.value) return null
    const roster = getRoster()
    if (!Array.isArray(roster)) return null
    return roster.find(e => e.agent_id === scopeId.value)?.start_span_id || null
  })

  // Unknown `?agent=` id: only a verdict once the roster has actually landed
  // — an empty-during-load roster must not flash "not found".
  const notFound = computed(() => !!scopeId.value
    && Array.isArray(getRoster())
    && !getRoster().some(e => e.agent_id === scopeId.value))

  // Deep-link limbo: `?agent=` is set but the roster hasn't landed, so the
  // scope can't resolve yet. The view masks the feed on this instead of
  // flashing the full main conversation before the scoped re-projection.
  // Once the roster is an array the scope is decided — found or notFound.
  const pending = computed(() => !!scopeId.value && !Array.isArray(getRoster()))

  function syncQuery() {
    if ((route.query.agent || null) === (scopeId.value || null)) return
    const query = { ...route.query }
    if (scopeId.value) query.agent = scopeId.value
    else delete query.agent
    router.replace({ query })
  }

  // Back/forward or an external ?agent= change re-enters/exits the scope.
  watch(() => route.query.agent, (v) => { scopeId.value = v || null })

  async function loadScopedSubtree(id) {
    loadingSubtree.value = true
    try { await ensureSpanSubtreeLoaded(id) }
    finally { loadingSubtree.value = false }
  }
  // Fires when the roster resolves the anchor — including the deep-link path
  // where scopeId is set long before the first /map response lands.
  watch(startSpanId, (id) => { if (scopeId.value && id) loadScopedSubtree(id) },
    { immediate: true })

  // Orphan roster entries (span_count > 0, start_span_id null: markers lost,
  // agent_id-tagged spans survive) have no anchor for a targeted deep fetch —
  // pull the full span map instead so the scoped attribute-partition fallback
  // can reach their spans. ensureTerminalSpansLoaded is idempotent.
  const orphanScope = computed(() => !!scopeId.value
    && !startSpanId.value
    && (scopedAgent.value?.spanCount || 0) > 0)
  async function loadOrphanSpans() {
    loadingSubtree.value = true
    try { await ensureTerminalSpansLoaded() }
    finally { loadingSubtree.value = false }
  }
  watch(orphanScope, (v) => { if (v) loadOrphanSpans() }, { immediate: true })

  function getScroller() {
    return document.querySelector('.content-scroll')
      || document.scrollingElement
      || document.documentElement
  }

  function enter(agentId) {
    if (!agentId || scopeId.value === agentId) return
    if (!scopeId.value) savedScrollTop = getScroller().scrollTop
    scopeId.value = agentId
    syncQuery()
  }

  async function exit() {
    scopeId.value = null
    syncQuery()
    if (savedScrollTop == null) return
    const top = savedScrollTop
    savedScrollTop = null
    await nextTick()
    getScroller().scrollTop = top
  }

  return reactive({
    scopeId, scopedAgent, startSpanId, notFound, pending, loadingSubtree,
    enter, exit,
  })
}
