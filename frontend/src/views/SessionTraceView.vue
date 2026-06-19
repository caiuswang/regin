<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Button from '../components/ui/Button.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import SessionTerminalLog from '../components/SessionTerminalLog.vue'
import SessionConversationView from '../components/SessionConversationView.vue'
import SuppressButton from '../components/triggers/SuppressButton.vue'
import { dropRetiredSpans } from '../utils/traceFormatters.js'
import { useTraceScroll } from '../composables/useTraceScroll.js'
import { useStickyHeader } from '../composables/useStickyHeader.js'
import { useViewMode } from '../composables/useViewMode.js'
import { useFilterState } from '../composables/useFilterState.js'
import { useRuleTriggers } from '../composables/useRuleTriggers.js'
import { useTraceTimeline } from '../composables/useTraceTimeline.js'
import { useCompactWatch } from '../composables/useCompactWatch.js'
import { useSpanContentCache } from '../composables/useSpanContentCache.js'
import { useToolRollup } from '../composables/useToolRollup.js'
import { useWorkflowMeta } from '../composables/useWorkflowMeta.js'
import { useTraceData } from '../composables/useTraceData.js'
import { useTurns } from '../composables/useTurns.js'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'
import ToolTokenRollup from '../components/ToolTokenRollup.vue'
import SessionTraceHeader from '../components/SessionTraceHeader.vue'
import TraceOverviewStrip from '../components/TraceOverviewStrip.vue'
import SpanDetailPanel from '../components/SpanDetailPanel.vue'
import { findNodeBySpanId, findNodePath, findNodeKey } from '../utils/spanTree.js'
import SessionTurnsSidebar from '../components/SessionTurnsSidebar.vue'
import SessionTimelineTree from '../components/SessionTimelineTree.vue'

const route = useRoute()
const session = ref(null)
const loading = ref(true)
const reloading = ref(false)
const lastReloadedAt = ref(null)
const selectedSpan = ref(null)
// Trigger map for the currently-selected rule.check span, plus the role gate
// for the suppress UI. Refetches on selection change (watch lives inside the
// composable) and after every suppress/unsuppress (call loadTriggersForSelectedSpan).
const { ruleTriggersByRuleId, canSuppressRule, loadTriggersForSelectedSpan } =
  useRuleTriggers(selectedSpan)

const expandedKeys = ref({})
const selectedKeys = ref({})   // PrimeVue TreeTable v-model:selection-keys

// Sticky page header: everything that frames the trace (title row, tokens
// rollup, mini-timeline, more-history banner) pins to the top of the scroll
// container so the user keeps navigation context while scrolling a long
// span list. Sidebar's sticky offset must match this height, so we measure
// the rendered header with a ResizeObserver and expose it as a CSS var.
// Re-measures on mount + whenever `loading` flips falsy (the v-else branch
// renders the sticky element only after session data lands).
const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

// On-demand span content cache; `allSpans` overlays it onto session.spans so
// every consumer reads one merged list (see useSpanContentCache).
const { spanContentCache, allSpans, fetchSpanContent } =
  useSpanContentCache(session, route)

// Trace data core: owns treeNodes + pagination + every fetch/merge/reconcile
// primitive, mutating the SFC-owned `session` and `selectedSpan` refs threaded
// in. See useTraceData (the live-tail reconcile is kept atomic there).
const {
  treeNodes,
  hasMoreOlder, loadingOlder,
  loadSession, reloadLiveTail, loadOlder,
  subtreeLoaded,
  ensureNodeChildrenLoaded, ensureSpanSubtreeLoaded,
  ensureTerminalSpansLoaded, ensureWorkflowSpansLoaded,
} = useTraceData(route, { session, allSpans, selectedSpan })

// View mode: 'conversation' | 'timeline' | 'terminal' | 'messages'.
// Resolution order: `?view=` query param > localStorage > default (see useViewMode).
const { viewMode, setViewMode } = useViewMode(route)

// The conversation tab defaults to the clean centered feed: the right rail
// (span details + turns) stays hidden until explicitly opened, so selecting
// a span doesn't squeeze the feed. Timeline/terminal keep the rail whenever
// a span is selected. Persisted so the choice survives navigation.
const detailRailOpen = useFilterState('regin.trace.detailRail', false,
  v => typeof v === 'boolean')

// send_to_user messages (Messages tab). Null until first load so the tab
// can distinguish "not fetched yet" from "fetched, empty". Refreshed by
// reload() while the tab is active, so the live poll keeps it current.
const agentMessages = ref(null)
const sessionGoal = ref(null)
async function ensureAgentMessagesLoaded() {
  const data = await api.get(`/sessions/${route.params.id}/agent-messages`)
  agentMessages.value = data.messages
  sessionGoal.value = data.session_goal
}

// Jump from a send_to_user span (right rail) to its rendered card in the
// Messages tab. The span and the agent message share a span_id, which anchors
// each <li>. Briefly highlight the target so it's findable after the scroll.
const highlightedMessageSpan = ref(null)
async function goToMessage(span) {
  if (!span?.span_id) return
  setViewMode('messages')
  await ensureAgentMessagesLoaded()
  await nextTick()
  const el = document.getElementById(`msg-${span.span_id}`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  highlightedMessageSpan.value = span.span_id
  setTimeout(() => {
    if (highlightedMessageSpan.value === span.span_id) highlightedMessageSpan.value = null
  }, 2400)
}

// Pill colour for a non-progress message type (matches InboxMessageCard).
const MESSAGE_TYPE_CLASS = {
  result: 'bg-emerald-100 text-emerald-700',
  summary: 'bg-indigo-100 text-indigo-700',
  warning: 'bg-amber-100 text-amber-800',
  blocker: 'bg-red-100 text-red-700',
  note: 'bg-slate-100 text-slate-600',
}
function messageTypeClass(t) {
  return MESSAGE_TYPE_CLASS[t] || 'bg-slate-100 text-slate-600'
}

// Header pivot metadata: plans this session authored, workflow runs it
// launched, and (when this session IS a run) its stale-snapshot marker +
// launching-session backlink. See useWorkflowMeta.
const {
  plans, workflowRuns, workflowRunsById,
  snapshotStaleAt, workflowParentTo,
  fetchPlans, fetchWorkflowRuns,
} = useWorkflowMeta(route, allSpans)

// Per-session tool/token rollup (server-side aggregate). See useToolRollup.
const { toolRollupData, fetchToolRollup } = useToolRollup(route)

// Scroll/wheel/touch-driven auto-reload (pull-to-refresh at the bottom,
// pull-older at the top). The composable owns the DOM mechanics + latches and
// attaches its own document listeners on mount; we hand it the loader
// callbacks and the gating refs it reads.
useTraceScroll({ reloading, loading, loadingOlder, hasMoreOlder, reload, loadOlder })

// General live poll. The trace view is a live dashboard but `reload()`
// otherwise only fires on scroll/wheel — so a user parked at the bottom
// watching their session never sees updates (and any transient duplicate
// from a placeholder→anchor handoff never gets reconciled away) until they
// scroll. A lightweight visibility-gated tick keeps the reconcile
// (`reloadLiveTail`) converging the tail to the DB every few seconds.
let livePollTimer = null
const LIVE_POLL_MS = 4000
function startLivePoll() {
  if (livePollTimer) return
  livePollTimer = setInterval(() => {
    if (document.hidden) return
    if (reloading.value || loading.value || loadingOlder.value) return
    reload()
  }, LIVE_POLL_MS)
}
function stopLivePoll() {
  if (livePollTimer) { clearInterval(livePollTimer); livePollTimer = null }
}

onMounted(async () => {
  const rollupP = fetchToolRollup()
  const plansP = fetchPlans()
  const wfRunsP = fetchWorkflowRuns()
  await loadSession()
  loading.value = false
  await Promise.all([rollupP, plansP, wfRunsP])
  startLivePoll()
  if (viewMode.value === 'terminal') ensureTerminalSpansLoaded()
  if (viewMode.value === 'messages') ensureAgentMessagesLoaded()
  // Scroll/wheel/touch auto-reload listeners are attached by useTraceScroll();
  // the sticky-header ResizeObserver is owned by useStickyHeader.
})

onUnmounted(() => {
  // Scroll/wheel/touch listeners are detached by useTraceScroll(); the
  // sticky-header observer + compact poll are torn down by their composables.
  stopLivePoll()
})

// When the user enters the Terminal tab (or lands on it via localStorage
// restore), fetch every span — not the shallow root-only set the other
// tabs use. Conversation tab also needs turns to render the right-rail
// timeline, so trigger that load here too.
watch(viewMode, async (mode) => {
  if (mode === 'terminal') {
    await ensureTerminalSpansLoaded()
  } else if (mode === 'messages') {
    await ensureAgentMessagesLoaded()
  } else if (mode === 'conversation') {
    if (turns.value == null && !turnsLoading.value) {
      turnsLoading.value = true
      try { await fetchTurns() } finally { turnsLoading.value = false }
    }
  }
})

async function reload() {
  if (reloading.value) return
  reloading.value = true
  try {
    const tasks = [reloadLiveTail(), fetchToolRollup()]
    // Messages tab rides the same live poll: cheap per-session query.
    if (viewMode.value === 'messages') tasks.push(ensureAgentMessagesLoaded())
    // Only refetch turns if they're loaded AND visible. While folded the
    // user isn't reading them, so defer the cost; mark stale and let the
    // unfold action pull the fresh copy in.
    if (turns.value != null && !turnsCollapsed.value) {
      tasks.push(fetchTurns())
    } else if (turns.value != null && turnsCollapsed.value) {
      turnsStale.value = true
    }
    await Promise.all(tasks)
    lastReloadedAt.value = new Date()
  } finally {
    reloading.value = false
  }
}

function latestSpanByTime(spans) {
  if (!spans?.length) return null
  return [...spans].sort((a, b) => {
    const at = a.start_time ? new Date(a.start_time).getTime() : 0
    const bt = b.start_time ? new Date(b.start_time).getTime() : 0
    if (at !== bt) return at - bt
    const aid = a.id || 0
    const bid = b.id || 0
    return aid - bid
  })[spans.length - 1] || null
}

async function jumpToLatestSpan() {
  setViewMode('conversation')
  await nextTick()
  await reloadLiveTail()
  const latest = latestSpanByTime(allSpans.value)
  if (latest) {
    if (selectedSpan.value?.span_id === latest.span_id) {
      selectedSpan.value = null
      await nextTick()
    }
    selectedSpan.value = latest
    if (!spanContentCache.value.has(latest.span_id) && latest.attributes && !Object.keys(latest.attributes).length) {
      fetchSpanContent(latest.span_id)
    }
  }
}

// Drive `compact.pre → compact.post` polling off the live span set.
useCompactWatch(allSpans, reload, { reloading, loading })

async function onNodeExpand(event) {
  const spanId = event?.node?.data?.span_id
  if (!spanId) return
  const nodeKey = event?.node?.key
  if (nodeKey) {
    expandedKeys.value = { ...expandedKeys.value, [nodeKey]: true }
  }
  await ensureNodeChildrenLoaded(spanId)
  if (nodeKey) {
    expandedKeys.value = { ...expandedKeys.value, [nodeKey]: true }
  }
}

async function toggleTimelineNode(node) {
  if (!node?.key || !node?.data?.span_id || node.leaf) return
  if (expandedKeys.value[node.key]) {
    const next = { ...expandedKeys.value }
    delete next[node.key]
    expandedKeys.value = next
    return
  }
  expandedKeys.value = { ...expandedKeys.value, [node.key]: true }
  await ensureNodeChildrenLoaded(node.data.span_id)
}

async function onOverviewSpanClick(node) {
  if (!node?.data?.span_id) return
  const spanId = node.data.span_id
  selectedSpan.value = allSpans.value.find(s => s.span_id === spanId) || node.data
  if (!node.leaf) {
    expandedKeys.value = { ...expandedKeys.value, [node.key]: true }
    await ensureNodeChildrenLoaded(spanId)
  }
  // Timeline + terminal rows live inside data-span-id-marked tables, so
  // the existing poll-and-scroll helper finds them. Conversation view
  // tracks DOM refs via promptRefs and scrolls itself off its own
  // selectedSpan watcher — calling scrollSpanRowIntoView there would
  // just spin out 20 polling attempts that never match anything.
  if (viewMode.value !== 'conversation') {
    await nextTick()
    scrollSpanRowIntoView(spanId)
  }
}

// Session-level timeline bounds (DB-anchored with a live edge) + active-work
// aggregate — see useTraceTimeline.
const { traceStart, traceEnd, traceDuration, activeWorkMs } =
  useTraceTimeline(session, allSpans)

// Select + scroll to a span by id, loading its (possibly collapsed)
// subtree first. Shared by the task-list jump and the tool-drill-down
// jump. If the span isn't in the loaded shallow set, walk the roots
// calling `ensureSpanSubtreeLoaded` until it materialises; the existing
// `selectedSpan` watcher then does the scroll-and-highlight.
async function selectSpanById(spanId) {
  if (!spanId) return
  setViewMode('conversation')
  let span = allSpans.value.find(s => s.span_id === spanId)
  if (!span) {
    for (const node of treeNodes.value) {
      if (node?.data?.span_id) {
        // eslint-disable-next-line no-await-in-loop
        await ensureSpanSubtreeLoaded(node.data.span_id)
        span = allSpans.value.find(s => s.span_id === spanId)
        if (span) break
      }
    }
  }
  if (span) selectedSpan.value = span
}

// Jump from a row in the expanded task list to the most relevant span for
// that task's current state: pending → TaskCreate, in_progress / completed
// → the TaskUpdate that flipped it. Backend pre-computes `current_span_id`;
// fall back to `created_span_id` for pending.
function jumpToTaskSpan(task) {
  return selectSpanById(task?.current_span_id || task?.created_span_id)
}

async function onNodeSelect(event) {
  const nodeData = event?.node?.data || event?.data
  if (!nodeData?.span_id) return
  const full = allSpans.value.find(s => s.span_id === nodeData.span_id)
  selectedSpan.value = full || nodeData

  const selectedNode = event?.node || findNodeBySpanId(treeNodes.value, nodeData.span_id)
  if (selectedNode && !selectedNode.leaf) {
    expandedKeys.value = { ...expandedKeys.value, [selectedNode.key]: true }
    await ensureNodeChildrenLoaded(nodeData.span_id)
  }
}

// Recursive lookup: `treeNodes` is the client-built hierarchy of root
// plus lazily-loaded children — find the node key for a span_id so
// we can drive PrimeVue's selection/expansion from a raw span ref.
// Keep the PrimeVue TreeTable's internal selection state in sync
// with the Vue-side `selectedSpan`. Without this the `.p-highlight`
// row decoration only fires when the user clicks the tree directly
// — clicking a strip bar, a turn row, or a drill-down ref would
// otherwise never light up the corresponding row.
watch(selectedSpan, async (span) => {
  if (!span) {
    selectedKeys.value = {}
    return
  }
  // Fetch content on-demand if this span's attributes aren't cached yet.
  if (!spanContentCache.value.has(span.span_id)) {
    await fetchSpanContent(span.span_id)
    // Re-bind selectedSpan to the fresh object from allSpans so the
    // details panel sees the newly-loaded attributes.
    const fresh = allSpans.value.find(s => s.span_id === span.span_id)
    if (fresh && fresh !== span) {
      selectedSpan.value = fresh
      return // watcher will fire again with the fresh object
    }
  }
  const key = findNodeKey(treeNodes.value, span.span_id)
  selectedKeys.value = key ? { [key]: true } : {}
})

// Turn-usage sidebar + the bidirectional turn⇄span cross-highlight. Called
// AFTER the selection watcher above on purpose: useTurns registers the
// span→turn watcher, which must fire *after* the content-fetch + selectedKeys
// sync so the row highlight lands before the turn scroll.
const {
  turns, turnsLoading, turnsCollapsed, turnsStale,
  selectedTurnUuid, expandedTurnUuid, maxTurnConsumption,
  spanIdsInSelectedTurn,
  fetchTurns, loadTurns, toggleTurnsCollapsed,
  selectTurn, toggleTurnExpanded, storeTurnRow, handleSpanRefClick,
} = useTurns(route, {
  allSpans, treeNodes, selectedSpan, selectedKeys, expandedKeys,
  ensureSpanSubtreeLoaded,
})

</script>

<template>
  <div v-if="loading" class="empty-state">Loading session…</div>
  <div v-else-if="!session || !allSpans.length" class="empty-state">
    No spans found for this session.
  </div>
  <div
    v-else
    class="trace-detail-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: title row, tokens rollup, mini-timeline and
         the "more history" affordance pin to the top of `.content-scroll`
         so the user keeps session context while scrolling a long span
         list. Negative margins match `.content-scroll`'s padding (mobile
         1rem, desktop 1.5rem top / 2rem sides) so the white background
         goes edge-to-edge of the content card without overshooting. The
         rendered height is measured by a ResizeObserver and propagated
         to the sidebar's sticky offset. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-4 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <SessionTraceHeader
      :key="session?.trace_id"
      :session="session"
      :plans="plans"
      :workflow-runs="workflowRuns"
      :view-mode="viewMode"
      :reloading="reloading"
      :loading="loading"
      :last-reloaded-at="lastReloadedAt"
      :has-turns="turns != null"
      :trace-duration="traceDuration"
      :active-work-ms="activeWorkMs"
      :snapshot-stale-at="snapshotStaleAt"
      :workflow-parent-to="workflowParentTo"
      @update:view-mode="setViewMode"
      @reload="reload"
      @jump-to-task="jumpToTaskSpan"
    />

    <ToolTokenRollup :rollup-data="toolRollupData" @jump-span="selectSpanById" />

    <TraceOverviewStrip
      :tree-nodes="treeNodes"
      :selected-span="selectedSpan"
      :selected-turn-uuid="selectedTurnUuid"
      :span-ids-in-selected-turn="spanIdsInSelectedTurn"
      :turns="turns"
      :trace-start="traceStart"
      :trace-end="traceEnd"
      :trace-duration="traceDuration"
      @select-node="onOverviewSpanClick"
    />

    <!-- Top indicator: only render when older history is available
         (or actively loading). Mirrors the bottom footer's
         infinite-feed pattern. -->
    <div
      v-if="hasMoreOlder || loadingOlder"
      class="mb-4 pb-3 border-b border-slate-200 flex items-center justify-center text-slate-400"
    >
      <span v-if="loadingOlder" class="inline-flex items-center gap-2 text-[12px]">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Loading older</span>
      </span>
      <span v-else class="text-[11px] tracking-wider uppercase">↑ More history above</span>
    </div>
    </div>
    <!-- /sticky page header -->

    <!-- Queued prompts: typed while the agent is busy fire no hook, so they
         can't show as spans; derived live from the transcript and transient —
         they vanish from here the moment the agent dequeues them. -->
    <div v-if="session?.queued_prompts?.length"
         class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
      <div class="text-[11px] font-semibold uppercase tracking-wider text-amber-700 mb-1">
        ⏳ {{ session.queued_prompts.length }} queued
      </div>
      <div v-for="(q, qi) in session.queued_prompts" :key="qi"
           class="text-sm text-amber-900 truncate" :title="q.content">
        {{ q.content }}
      </div>
    </div>

    <div class="flex flex-col lg:flex-row gap-4 lg:items-start">
      <!-- Conversation view: rendered outside Card so its sidebar can be sticky -->
      <template v-if="viewMode === 'conversation'">
        <SessionConversationView
          :spans="allSpans"
          :turns="turns"
          :selected-span="selectedSpan"
          :trace-id="session?.trace_id"
          :context-window-tokens="session?.context_window_tokens"
          :workflow-runs-by-id="workflowRunsById"
          :loaded-subtrees="subtreeLoaded"
          class="flex-1 min-w-0"
          @select-span="selectedSpan = $event"
          @fetch-content="fetchSpanContent"
          @load-subtree="ensureSpanSubtreeLoaded"
          @jump-live="jumpToLatestSpan"
        />
      </template>

      <template v-else>
        <Card :no-padding="true" class="trace-content-card flex-1 min-w-0 w-full">
          <!-- Timeline view: TreeTable -->
          <template v-if="viewMode === 'timeline'">
            <SessionTimelineTree
              :tree-nodes="treeNodes"
              v-model:expanded-keys="expandedKeys"
              v-model:selection-keys="selectedKeys"
              @node-select="onNodeSelect"
              @toggle-node="toggleTimelineNode"
            />
          </template>

          <!-- Terminal view: flat log -->
          <template v-else-if="viewMode === 'terminal'">
            <SessionTerminalLog
              :spans="allSpans"
              :turns="turns"
              :selected-span="selectedSpan"
              @select-span="selectedSpan = $event"
              @fetch-content="fetchSpanContent"
              @load-subtree="ensureSpanSubtreeLoaded"
            />
          </template>

          <!-- Messages view: send_to_user feed as a vertical timeline -->
          <template v-else-if="viewMode === 'messages'">
            <div class="px-4 py-8 lg:px-8">
              <div v-if="agentMessages == null" class="text-slate-500 text-sm py-16 text-center">
                Loading messages…
              </div>
              <div v-else-if="!agentMessages.length" class="text-slate-500 text-sm py-16 text-center">
                No send_to_user messages in this session.
              </div>
              <div v-else class="w-full">
                <div
                  v-if="sessionGoal"
                  class="mb-8 rounded-lg border border-slate-200 bg-slate-50 px-5 py-4"
                >
                  <div class="flex items-center gap-1.5 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    <svg class="w-3.5 h-3.5 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" />
                    </svg>
                    Session goal
                  </div>
                  <div class="text-sm text-slate-900 leading-relaxed whitespace-pre-wrap">{{ sessionGoal }}</div>
                </div>
                <ol class="relative ml-2 border-l-2 border-slate-100 space-y-7 pb-2">
                  <li
                    v-for="(m, i) in agentMessages"
                    :key="m.id ?? m.span_id"
                    :id="m.span_id ? `msg-${m.span_id}` : undefined"
                    class="relative pl-7 scroll-mt-28"
                  >
                    <span
                      class="absolute -left-[9px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white border-2 transition-colors duration-200"
                      :class="i === agentMessages.length - 1 ? 'border-blue-500' : 'border-slate-300'"
                    >
                      <span
                        class="h-1.5 w-1.5 rounded-full"
                        :class="i === agentMessages.length - 1 ? 'bg-blue-500' : 'bg-slate-300'"
                      ></span>
                    </span>
                    <div class="flex items-baseline gap-2 mb-1.5">
                      <span class="text-xs font-semibold text-slate-700">#{{ i + 1 }}</span>
                      <span
                        v-if="m.msg_type && m.msg_type !== 'progress'"
                        class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                        :class="messageTypeClass(m.msg_type)"
                      >{{ m.msg_type }}</span>
                      <span v-if="m.title" class="text-xs font-semibold text-slate-800">{{ m.title }}</span>
                      <span class="text-[11px] font-mono text-slate-500">
                        {{ new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }}
                      </span>
                    </div>
                    <div
                      class="rounded-lg border bg-white px-4 py-3 shadow-sm transition-colors duration-200"
                      :class="highlightedMessageSpan === m.span_id
                        ? 'border-blue-400 ring-2 ring-blue-300'
                        : 'border-slate-200 hover:border-slate-300'"
                    >
                      <MarkdownContent :markdown="m.body" />
                    </div>
                  </li>
                </ol>
              </div>
            </div>
          </template>
        </Card>
      </template>

      <!-- Span detail rail is irrelevant on the Messages tab (no span
           selection there) and would squeeze the centered feed. On the
           Conversation tab it is opt-in (detailRailOpen) for the same
           density reason; the sticky tab below reopens it. -->
      <Button
        v-if="viewMode === 'conversation' && selectedSpan && !detailRailOpen"
        variant="ghost"
        class="sticky self-start shrink-0 z-10 gap-1 px-2 py-1.5 h-auto rounded-md border border-slate-200 bg-white text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
        :style="{ top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem' }"
        aria-label="Show span details"
        @click="detailRailOpen = true"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 18 9 12 15 6" />
        </svg>
        Details
      </Button>
      <aside
        v-if="selectedSpan && viewMode !== 'messages' && (viewMode !== 'conversation' || detailRailOpen)"
        class="w-full lg:w-96 lg:shrink-0 lg:sticky lg:self-start lg:overflow-y-auto z-10"
        :style="{
          /* Page header is sticky-pinned with top: -1.5rem (lg padding-top)
             so its background covers .content-scroll padding-top. The
             sidebar pins flush under it: `header_h - 1.5rem + small gap`. */
          top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem',
          maxHeight: stickyHeaderHeight ? `calc(100vh - ${stickyHeaderHeight}px - 2rem)` : 'calc(100vh - 6rem)',
        }"
      >
        <div v-if="viewMode === 'conversation'" class="flex justify-end mb-2">
          <Button
            variant="ghost"
            class="gap-1 px-2 py-1 h-auto rounded-md border border-slate-200 bg-white text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
            aria-label="Hide span details"
            @click="detailRailOpen = false"
          >
            Hide
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </Button>
        </div>
        <SpanDetailPanel
          :key="selectedSpan && selectedSpan.span_id"
          :selected-span="selectedSpan"
          :rule-triggers-by-rule-id="ruleTriggersByRuleId"
          :can-suppress-rule="canSuppressRule"
          :workflow-runs-by-id="workflowRunsById"
          @suppress-changed="loadTriggersForSelectedSpan"
          @view-message="goToMessage"
        />

        <SessionTurnsSidebar
          :turns="turns"
          :turns-collapsed="turnsCollapsed"
          :turns-stale="turnsStale"
          :turns-loading="turnsLoading"
          :selected-turn-uuid="selectedTurnUuid"
          :expanded-turn-uuid="expandedTurnUuid"
          :selected-span="selectedSpan"
          :max-turn-consumption="maxTurnConsumption"
          @load="loadTurns"
          @toggle-collapsed="toggleTurnsCollapsed"
          @toggle-expanded="toggleTurnExpanded"
          @select-turn="selectTurn"
          @select-span-ref="handleSpanRefClick"
          @store-row="storeTurnRow"
        />
      </aside>
    </div>
    <!-- Infinite-feed-style footer: spinner during reload, otherwise
         a quiet end-of-timeline marker. Same pattern as Twitter/IG,
         no instructional text. -->
    <div class="mt-8 mb-4 flex items-center justify-center text-slate-400">
      <span v-if="reloading" class="inline-flex items-center gap-2 text-[12px]">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Loading</span>
      </span>
      <span v-else class="text-[11px] tracking-wider uppercase">
        End of timeline
      </span>
    </div>
  </div>
</template>

<style scoped>
/* Make the TreeTable column headers (Span / Time / Tokens) pin under
   the sticky page header so they stay visible while scrolling a long
   span list. Card and PrimeVue both wrap the table in an overflow-auto
   container that would otherwise trap `position: sticky` inside the
   card; we override those to `overflow: visible` so sticky resolves to
   `.content-scroll` instead. `--regin-trace-header-h` is set on the
   root by the ResizeObserver in <script>. */
.trace-detail-root :deep(.trace-content-card.card) {
  overflow: visible !important;
}
.trace-detail-root :deep(.p-treetable-table-container) {
  overflow: visible !important;
}
.trace-detail-root :deep(.p-treetable-thead > tr > th) {
  position: sticky;
  /* Page header pins at `top: -1rem` (mobile) / `-1.5rem` (desktop) so its
     opaque background covers `.content-scroll`'s padding-top; the thead
     pins flush below it, so subtract the same offset. */
  top: calc(var(--regin-trace-header-h, 0px) - 1rem);
  z-index: 5;
  background: var(--color-white);
}
@media (min-width: 1024px) {
  .trace-detail-root :deep(.p-treetable-thead > tr > th) {
    top: calc(var(--regin-trace-header-h, 0px) - 1.5rem);
  }
}
</style>
