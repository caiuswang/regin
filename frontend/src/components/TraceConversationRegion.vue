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
import { onMounted, onUnmounted } from 'vue'
import SessionConversationView from './SessionConversationView.vue'
import TraceAgentPane from './TraceAgentPane.vue'

const props = defineProps({
  // useTraceScope reactive object (scopeId, scopedAgent, notFound, pending,
  // loadingSubtree, active, enter, exit, …).
  traceScope: { type: Object, required: true },
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
  //   workflowRunsById, loadedSubtrees, serverNow, serverNowAt }
  feed: { type: Object, required: true },
  stickyTop: { type: Number, default: 0 },
})
const emit = defineEmits([
  'select-span', 'fetch-content', 'load-subtree', 'jump-live', 'enter-scope', 'exit', 'expand',
])

// Esc exits the scope (both the split pane and the <xl takeover) — the
// keyboard sibling of the pane ✕ / scope-bar ✕. Only acts while a scope or the
// roster is open, so it never swallows Escape for the rest of the view.
function onKeydown(e) {
  if (e.key === 'Escape' && props.traceScope.active) emit('exit')
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
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
    :spans="feed.spans"
    :turns="feed.turns"
    :selected-span="feed.selectedSpan"
    :trace-id="feed.traceId"
    :context-window-tokens="feed.contextWindowTokens"
    :workflow-runs-by-id="feed.workflowRunsById"
    :loaded-subtrees="feed.loadedSubtrees"
    :scope-agent="takeover ? traceScope.scopedAgent : null"
    :scope-loading="traceScope.loadingSubtree"
    :scoped-agent-id="(!takeover && isXl && traceScope.scopeId) ? traceScope.scopeId : ''"
    :hide-toc="hideToc"
    class="flex-1 min-w-0 xl:min-w-72"
    @select-span="emit('select-span', $event)"
    @fetch-content="emit('fetch-content', $event)"
    @load-subtree="emit('load-subtree', $event)"
    @jump-live="emit('jump-live')"
    @enter-scope="emit('enter-scope', $event)"
  />

  <TraceAgentPane
    v-if="paneVisible"
    :mode="traceScope.rosterOpen ? 'roster' : 'scope'"
    :agent="traceScope.scopedAgent"
    :not-found="traceScope.notFound"
    :scope-loading="traceScope.loadingSubtree"
    :running-agents="liveAgents.runningAgents"
    :finished-agents="liveAgents.finishedAgents"
    :spans="feed.spans"
    :turns="feed.turns"
    :selected-span="feed.selectedSpan"
    :trace-id="feed.traceId"
    :context-window-tokens="feed.contextWindowTokens"
    :workflow-runs-by-id="feed.workflowRunsById"
    :loaded-subtrees="feed.loadedSubtrees"
    :server-now="feed.serverNow"
    :server-now-at="feed.serverNowAt"
    :sticky-top="stickyTop"
    @exit="emit('exit')"
    @scope="emit('enter-scope', $event)"
    @expand="emit('expand')"
    @select-span="emit('select-span', $event)"
    @fetch-content="emit('fetch-content', $event)"
    @load-subtree="emit('load-subtree', $event)"
    @jump-live="emit('jump-live')"
  />
</template>
