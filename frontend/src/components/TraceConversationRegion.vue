<script setup>
// The Conversation tab's feed region, factored out of SessionTraceView so the
// responsive agent-scope branching lives in one place (and off the host's
// template-complexity budget). One `?agent=` scope, two layouts:
//
//   <xl  — full-feed TAKEOVER: the feed itself renders the scoped projection
//          (scope-agent set); the deep-link limbo mask hides it until the
//          roster lands.
//   ≥xl  — SPLIT: the feed stays UNSCOPED (full main thread) and only
//          highlights the originating card; the scoped projection lives in the
//          companion TraceAgentPane beside it.
//
// `feed` bundles the shared span/turn inputs as one object so the two feed
// consumers don't each spell out a dozen binds.
import { onMounted, onUnmounted, nextTick, ref, computed, watch } from 'vue'
import SessionConversationView from './SessionConversationView.vue'
import TraceAgentPane from './TraceAgentPane.vue'

const props = defineProps({
  // useTraceScope reactive object (scopeId, scopedAgent, notFound, pending,
  // active, enter, exit, …).
  traceScope: { type: Object, required: true },
  // useAgentTrace: the scoped agent's OWN spans, fetched from its child
  // trace. Neither scoped surface can re-project `feed.spans` any more — a
  // delegating parent's conversation keeps only the agent's markers.
  agentSpans: { type: Array, default: () => [] },
  agentLoading: { type: Boolean, default: false },
  // useLiveAgents reactive object (runningAgents / finishedAgents for the
  // pane roster mode).
  liveAgents: { type: Object, required: true },
  isXl: { type: Boolean, default: false },
  // The scoped feed is rendered full-width (the <xl takeover, OR the ≥xl
  // 'full' mode the user chose). When false at ≥xl the feed stays unscoped and
  // the scope lives in the companion pane.
  takeover: { type: Boolean, default: false },
  paneVisible: { type: Boolean, default: false },
  // xl-but-not-2xl with the pane open: the TOC rail yields so the feed keeps
  // a readable width (three columns only fit at ≥2xl).
  hideToc: { type: Boolean, default: false },
  // { spans, turns, selectedSpan, traceId, contextWindowTokens,
  //   workflowRunsById, loadedSubtrees, serverNow, serverNowAt,
  //   followUp, sendSeq }
  feed: { type: Object, required: true },
  stickyTop: { type: Number, default: 0 },
  // The presentation reader is open over the whole viewport.
  readerOpen: { type: Boolean, default: false },
})
const emit = defineEmits([
  'select-span', 'fetch-content', 'load-subtree', 'load-subtrees', 'jump-live', 'enter-scope', 'exit', 'expand',
  'present', 'follow-latest', 'resumed',
])

// Which of the two feeds raised the current selection. The split mounts
// SessionConversationView twice off ONE `selectedSpan`, so without this a click
// in the pane reached the main feed's cross-highlight watcher and unfolded the
// agent + scrolled the main thread to a row the reader never asked for (and
// re-fired on every live poll). Anything not raised here — timeline click,
// `?span=` deep link, a detail-rail span ref — stays 'external' and keeps the
// original follow-the-selection behaviour on both feeds.
const selectionOrigin = ref('external')
let claimed = false
function onSelect(origin, span) {
  selectionOrigin.value = origin
  claimed = true
  emit('select-span', span)
  nextTick(() => { claimed = false })
}
watch(() => props.feed.selectedSpan?.span_id, () => {
  if (!claimed) selectionOrigin.value = 'external'
})

// The pane's embedded feed shows no follow pill of its own — it mirrors the
// main feed's follow-tail (exposed by SessionConversationView), so following
// the session also keeps the scoped agent view stuck to its newest span.
const mainFeed = ref(null)
const mainFollowing = computed(() => !!mainFeed.value?.followTail)

// Esc exits the scope (both the split pane and the <xl takeover) — the
// keyboard sibling of the pane ✕ / scope-bar ✕. Only acts while a scope or the
// roster is open, so it never swallows Escape for the rest of the view.
function onKeydown(e) {
  // While the presentation reader owns the viewport it swallows every key —
  // Escape there means "close the reader", not "leave the agent scope".
  if (props.readerOpen) return
  if (e.key === 'Escape' && props.traceScope.active) emit('exit')
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
// One object, forwarded whole — the feed and the span reader's docked composer
// read the same bundle, so neither surface can be handed a subset of it. The
// takeover case is this region's own call, not the bundle's: a feed scoped to
// one agent's subtree is not the session, so it offers no follow-up.
const feedFollowUp = computed(() => (props.takeover
  ? { ...props.feed.followUp, canFollowUp: false, canResume: false }
  : props.feed.followUp))
</script>

<template>
  <!-- Deep-link limbo (<xl only): `?agent=` set but the roster hasn't landed —
       mask the feed rather than flash the full thread then snap to scoped. At
       ≥xl the main feed stays visible and the pane shows the loading state. -->
  <div
    v-if="takeover && traceScope.pending"
    class="flex-1 min-w-0 text-slate-400 text-center py-8"
    data-testid="trace-scope-pending"
  >Loading agent scope…</div>
  <SessionConversationView
    v-else
    ref="mainFeed"
    :spans="takeover && traceScope.scopedAgent ? agentSpans : feed.spans"
    :turns="feed.turns"
    :selected-span="feed.selectedSpan"
    :trace-id="feed.traceId"
    :context-window-tokens="feed.contextWindowTokens"
    :workflow-runs-by-id="feed.workflowRunsById"
    :loaded-subtrees="feed.loadedSubtrees"
    :task-events="feed.taskEvents"
    :scope-agent="takeover ? traceScope.scopedAgent : null"
    :scope-loading="agentLoading"
    :scoped-agent-id="(!takeover && isXl && traceScope.scopeId) ? traceScope.scopeId : ''"
    :agents="liveAgents.agents"
    :server-now="feed.serverNow"
    :server-now-at="feed.serverNowAt"
    :hide-toc="hideToc"
    :follow-up="feedFollowUp"
    :send-seq="feed.sendSeq"
    :background-shells="feed.backgroundShells"
    :follow-selection="selectionOrigin !== 'pane'"
    class="flex-1 min-w-0 xl:min-w-72"
    data-testid="trace-conversation-feed"
    @select-span="onSelect('feed', $event)"
    @fetch-content="emit('fetch-content', $event)"
    @load-subtree="emit('load-subtree', $event)"
    @load-subtrees="emit('load-subtrees', $event)"
    @jump-live="emit('jump-live')"
    @enter-scope="emit('enter-scope', $event)"
    @present="emit('present', $event)"
    @follow-latest="emit('follow-latest')"
    @resumed="emit('resumed')"
  />

  <TraceAgentPane
    v-if="paneVisible"
    :mode="traceScope.rosterOpen ? 'roster' : 'scope'"
    :agent="traceScope.scopedAgent"
    :not-found="traceScope.notFound"
    :scope-loading="agentLoading"
    :running-agents="liveAgents.runningAgents"
    :finished-agents="liveAgents.finishedAgents"
    :agents="liveAgents.agents"
    :spans="agentSpans"
    :turns="feed.turns"
    :selected-span="feed.selectedSpan"
    :trace-id="feed.traceId"
    :context-window-tokens="feed.contextWindowTokens"
    :workflow-runs-by-id="feed.workflowRunsById"
    :loaded-subtrees="feed.loadedSubtrees"
    :task-events="feed.taskEvents"
    :server-now="feed.serverNow"
    :server-now-at="feed.serverNowAt"
    :sticky-top="stickyTop"
    :follow-selection="selectionOrigin !== 'feed'"
    :follow-active="mainFollowing"
    @exit="emit('exit')"
    @scope="emit('enter-scope', $event)"
    @expand="emit('expand')"
    @select-span="onSelect('pane', $event)"
    @fetch-content="emit('fetch-content', $event)"
    @load-subtree="emit('load-subtree', $event)"
    @load-subtrees="emit('load-subtrees', $event)"
    @jump-live="emit('jump-live')"
    @present="emit('present', $event)"
  />
</template>
