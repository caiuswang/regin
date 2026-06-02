import { ref, computed, watch, nextTick } from 'vue'
import api from '../api'
import { findNodePath, findNodeKey } from '../utils/spanTree.js'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'

// Turn-usage sidebar + the bidirectional turn⇄span cross-highlight.
//
// Shared refs threaded in (never hoisted): `allSpans`/`treeNodes` are the
// loaded span data, `selectedSpan` is the SFC-owned selection, and
// `selectedKeys`/`expandedKeys` are the PrimeVue tree-view state that selectTurn
// drives directly. `ensureSpanSubtreeLoaded` lazy-loads a prompt's subtree so a
// turn's target tool span exists in the tree before we scroll to it.
//
// IMPORTANT registration order: this composable registers the span→turn
// watcher. The SFC's content-fetch + selectedKeys watcher MUST be registered
// before calling useTurns() so it still fires first.
export function useTurns(route, {
  allSpans, treeNodes, selectedSpan, selectedKeys, expandedKeys,
  ensureSpanSubtreeLoaded,
}) {
  const turns = ref(null)  // lazy-loaded via /api/sessions/:id/turn-usage
  const turnsLoading = ref(false)
  const turnsCollapsed = ref(false)       // fold the loaded turns list back up
  const turnsStale = ref(false)           // a reload happened while folded; refetch on unfold
  const selectedTurnUuid = ref(null)      // which turn row is active
  const expandedTurnUuid = ref(null)      // which turn row is drilled down
  const turnRowRefs = {}                  // turn_uuid → <tr> element, for scroll-into-view

  // Largest (input + cache_creation + output) across the session, for scaling
  // per-row consumption bars. Populated from the API response so the client
  // doesn't reduce over the full turn list on every render.
  const maxTurnConsumption = ref(0)

  // Guard: when the user clicks a turn, we also set `selectedSpan` to the
  // turn's owning span. The span→turn watcher below would otherwise map that
  // back to a *different* turn and overwrite the one just picked. This flag
  // pauses that watcher for one microtask window — it must stay co-located
  // with both its writer (selectTurn) and the watcher it gates.
  let suppressSpanToTurnSync = false

  async function fetchTurns() {
    const res = await api.get(`/sessions/${route.params.id}/turn-usage`)
    turns.value = res.turns || []
    maxTurnConsumption.value = res.max_consumption_tokens || 0
  }

  async function loadTurns() {
    if (turns.value || turnsLoading.value) return
    turnsLoading.value = true
    try {
      await fetchTurns()
    } finally {
      turnsLoading.value = false
    }
  }

  async function toggleTurnsCollapsed() {
    turnsCollapsed.value = !turnsCollapsed.value
    if (!turnsCollapsed.value && turnsStale.value && !turnsLoading.value) {
      turnsStale.value = false
      turnsLoading.value = true
      try {
        await fetchTurns()
      } finally {
        turnsLoading.value = false
      }
    }
  }

  // Timestamp mixing note: `turn.timestamp` is UTC (`…Z`) from the transcript;
  // `span.start_time` is naive local from `datetime.now().isoformat()` in the
  // hooks. Both become correct epoch-ms through the JS Date parser — `Z` → UTC,
  // no-tz → local — so the millisecond math works without normalization.
  function turnStartMs(turn) {
    return turn && turn.timestamp ? new Date(turn.timestamp).getTime() : null
  }

  const selectedTurn = computed(() => {
    if (!turns.value || !selectedTurnUuid.value) return null
    return turns.value.find(t => t.turn_uuid === selectedTurnUuid.value) || null
  })

  // Which first-class (root) span(s) does the selected turn overlap? Tool-level
  // spans live *under* prompts, but the overview strip only renders roots — so
  // matching `span_refs` by id would never hit any strip bar. Instead compute
  // overlap between each root's time range and the turn's interval
  // `(prev_turn.ts, this_turn.ts]`.
  const selectedTurnInterval = computed(() => {
    if (!selectedTurn.value || !turns.value) return null
    const idx = turns.value.findIndex(t => t.turn_uuid === selectedTurn.value.turn_uuid)
    if (idx < 0) return null
    const end = turnStartMs(turns.value[idx])
    const start = idx > 0 ? turnStartMs(turns.value[idx - 1]) : -Infinity
    if (end == null) return null
    return { start, end }
  })

  const spanIdsInSelectedTurn = computed(() => {
    const ids = new Set()
    const iv = selectedTurnInterval.value
    if (!iv || !treeNodes.value.length) return ids
    for (const node of treeNodes.value) {
      const s = new Date(node.data.start_time).getTime()
      const e = node.data.end_time
        ? new Date(node.data.end_time).getTime()
        : s
      // Overlap test: [s, e] intersects (iv.start, iv.end].
      if (e > iv.start && s <= iv.end) ids.add(node.data.span_id)
    }
    return ids
  })

  function turnForSpan(span) {
    if (!turns.value || !turns.value.length || !span || !span.start_time) return null
    const t = new Date(span.start_time).getTime()
    // Turn N's interval is `(turn[N-1].ts, turn[N].ts]`; the first turn owns
    // everything before it as well, matching the backend.
    for (const turn of turns.value) {
      const ts = turnStartMs(turn)
      if (ts == null) continue
      if (t <= ts) return turn
    }
    return null
  }

  async function selectTurn(turnUuid) {
    if (selectedTurnUuid.value === turnUuid) {
      selectedTurnUuid.value = null
      expandedTurnUuid.value = null
      return
    }
    selectedTurnUuid.value = turnUuid

    // Jump to the actual tool span this turn produced — not the root prompt
    // that contains it. The backend attached `span_refs` (the tool/skill/edit
    // activity in this turn's interval); we land selectedSpan on the first so
    // the details panel shows *what* happened. The tool spans live under a
    // prompt, so we also expand that prompt (lazy-fetching its children on the
    // first visit) — otherwise the target row never mounts and nothing scrolls.
    const turn = turns.value?.find(t => t.turn_uuid === turnUuid)
    if (!turn || !treeNodes.value.length) return

    // Empty-tool-use turns (pure text responses) have no span_refs. Fall back
    // to the owning prompt so the click still lands somewhere.
    const firstRef = turn.span_refs?.[0]
    if (!firstRef) {
      const rootIds = spanIdsInSelectedTurn.value
      const root = treeNodes.value.find(n => rootIds.has(n.data.span_id))
      if (root) {
        suppressSpanToTurnSync = true
        selectedSpan.value = root.data
        await nextTick()
        suppressSpanToTurnSync = false
      }
      return
    }

    // Find the root (prompt) whose interval contains this tool span.
    const refMs = new Date(firstRef.start_time).getTime()
    const rootNode = treeNodes.value.find(n => {
      const s = new Date(n.data.start_time).getTime()
      const e = n.data.end_time ? new Date(n.data.end_time).getTime() : s
      return refMs >= s && refMs <= e
    })

    if (rootNode) {
      // Load the entire subtree so the target tool span exists somewhere in
      // treeNodes — it may be a grandchild (subagent → tool).
      await ensureSpanSubtreeLoaded(rootNode.data.span_id)
      // Then expand every ancestor on the path to the target so the row
      // actually renders in the DOM (PrimeVue only mounts visible rows).
      const path = findNodePath(treeNodes.value, firstRef.span_id)
      if (path && path.length) {
        const next = { ...expandedKeys.value }
        for (const node of path.slice(0, -1)) next[node.key] = true
        expandedKeys.value = next
      }
    }

    // Resolve the full span object if lazy-fetch surfaced it; otherwise fall
    // back to the minimal shim so the details panel still renders something.
    await nextTick()
    const full = (allSpans.value || []).find(s => s.span_id === firstRef.span_id)
    const target = full || {
      span_id: firstRef.span_id,
      name: firstRef.name,
      start_time: firstRef.start_time,
      end_time: firstRef.start_time,
      kind: 'internal',
      status_code: 'UNSET',
      attributes: firstRef.tool_name ? { tool_name: firstRef.tool_name } : {},
      duration_ms: 0,
    }

    suppressSpanToTurnSync = true
    selectedSpan.value = target
    // Set selectedKeys here so PrimeVue stamps `.p-highlight` immediately. The
    // selectedSpan watcher would otherwise do it, but only after an async
    // fetchSpanContent.
    const targetKey = findNodeKey(treeNodes.value, target.span_id)
    if (targetKey) selectedKeys.value = { [targetKey]: true }
    await nextTick()
    suppressSpanToTurnSync = false

    // Lazy-loaded child rows take a few cycles to mount + lay out in PrimeVue.
    // Poll for the row marked with the target span_id instead of guessing.
    scrollSpanRowIntoView(target.span_id)
  }

  function toggleTurnExpanded(turnUuid) {
    expandedTurnUuid.value = expandedTurnUuid.value === turnUuid ? null : turnUuid
  }

  function storeTurnRow(turnUuid, el) {
    if (el) turnRowRefs[turnUuid] = el
    else delete turnRowRefs[turnUuid]
  }

  function handleSpanRefClick(spanRef) {
    // The drill-down list shows span_refs from the backend turn-usage response
    // — minimal shape (span_id, name, start_time, tool_name) without attributes
    // or duration. Prefer a fuller copy from allSpans when one has landed; fall
    // back to the ref so the details panel still shows name + start.
    const cached = (allSpans.value || []).find(s => s.span_id === spanRef.span_id)
    if (cached) {
      selectedSpan.value = cached
      return
    }
    selectedSpan.value = {
      span_id: spanRef.span_id,
      name: spanRef.name,
      start_time: spanRef.start_time,
      end_time: spanRef.start_time,
      kind: 'internal',
      status_code: 'UNSET',
      attributes: spanRef.tool_name ? { tool_name: spanRef.tool_name } : {},
      duration_ms: 0,
    }
  }

  // span → turn half of the cross-highlight: when the user picks a span,
  // surface its owning turn in the sidebar (scroll + select). The turn → span
  // half is spanIdsInSelectedTurn above.
  watch(selectedSpan, async (span) => {
    if (suppressSpanToTurnSync) return
    if (!span || !turns.value) return
    const turn = turnForSpan(span)
    if (!turn) return
    selectedTurnUuid.value = turn.turn_uuid
    await nextTick()
    const row = turnRowRefs[turn.turn_uuid]
    if (row && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'nearest' })
    }
  })

  return {
    turns, turnsLoading, turnsCollapsed, turnsStale,
    selectedTurnUuid, expandedTurnUuid, maxTurnConsumption,
    spanIdsInSelectedTurn,
    fetchTurns, loadTurns, toggleTurnsCollapsed,
    selectTurn, toggleTurnExpanded, storeTurnRow, handleSpanRefClick,
  }
}
