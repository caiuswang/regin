<script setup>
// The agent companion pane — the ≥xl master–detail counterpart of the <xl
// TraceScopeBar takeover. Sits BESIDE the still-visible main conversation
// feed and hosts one `?agent=` scope (or the roster picker) in its own scroll
// container, pinned under the sticky page header like the detail rail. Two
// modes off the same state machine: 'scope' (an agent's task-prompt card +
// its subtree, via the shared scoped SessionConversationView projection) and
// 'roster' (running-first picker). Divergence states are first-class:
// span_count 0 → terminal empty (in SessionConversationView), fetch-in-flight
// → spinner, unknown ?agent= id → not-found — never a false empty.
import { ref, computed } from 'vue'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import TraceScopeIdentity from './TraceScopeIdentity.vue'
import TraceAgentRoster from './TraceAgentRoster.vue'
import SessionConversationView from './SessionConversationView.vue'

const props = defineProps({
  // 'scope' | 'roster'
  mode: { type: String, default: 'scope' },
  // useLiveAgents roster entry for the scoped agent, or null while unresolved.
  agent: { type: Object, default: null },
  notFound: { type: Boolean, default: false },
  scopeLoading: { type: Boolean, default: false },
  // Roster-mode lists.
  runningAgents: { type: Array, default: () => [] },
  finishedAgents: { type: Array, default: () => [] },
  // Scoped-feed inputs (forwarded to SessionConversationView).
  spans: { type: Array, default: () => [] },
  turns: { type: Array, default: null },
  selectedSpan: { type: Object, default: null },
  traceId: { type: String, default: '' },
  contextWindowTokens: { type: Number, default: null },
  workflowRunsById: { type: Object, default: () => ({}) },
  loadedSubtrees: { default: () => new Set() },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
  // Rendered sticky-header height (px) — the pane pins flush under it, mirroring
  // the detail rail's offset math.
  stickyTop: { type: Number, default: 0 },
})
const emit = defineEmits([
  'exit', 'scope', 'expand', 'select-span', 'fetch-content', 'load-subtree', 'jump-live',
])

// The pane element IS the scroll container (xl:overflow-y-auto) — handed to
// the embedded feed so its pin/follow machinery scrolls the pane, never the
// page-level `.content-scroll`.
const paneEl = ref(null)
function getPaneScroller() { return paneEl.value }

const isRoster = computed(() => props.mode === 'roster')
// Scope mode but the roster hasn't resolved the id yet (deep-link limbo):
// show a spinner in the pane rather than a false empty.
const pending = computed(() =>
  !isRoster.value && !props.notFound && !props.agent)
const rosterCount = computed(() =>
  props.runningAgents.length + props.finishedAgents.length)

const paneStyle = computed(() => ({
  top: props.stickyTop ? `calc(${props.stickyTop}px - 1rem)` : '5rem',
  maxHeight: props.stickyTop
    ? `calc(100vh - ${props.stickyTop}px - 2rem)`
    : 'calc(100vh - 6rem)',
}))
</script>

<template>
  <aside
    ref="paneEl"
    class="w-full xl:w-auto xl:basis-[45%] xl:min-w-[480px] xl:max-w-[560px] xl:shrink-0 xl:sticky xl:self-start xl:overflow-y-auto rounded-lg border border-violet-200 bg-white shadow-sm"
    :style="paneStyle"
    data-testid="trace-agent-pane"
    aria-label="Agent companion pane"
  >
    <!-- Sticky pane header: agent identity (or the roster title) + close.
         Pins to the top of THIS pane's scroll via the z-sticky token. -->
    <div class="z-sticky sticky top-0 flex items-center gap-2 border-b border-violet-200 bg-violet-50 px-3 py-2 text-[12px] text-violet-800">
      <template v-if="isRoster">
        <span class="text-violet-500 shrink-0" aria-hidden="true">◈</span>
        <span class="font-semibold flex-1 min-w-0">Agents</span>
        <span class="font-mono text-[11px] text-violet-600 shrink-0">{{ rosterCount }}</span>
      </template>
      <TraceScopeIdentity
        v-else
        :agent="agent"
        :not-found="notFound"
        :server-now="serverNow"
        :server-now-at="serverNowAt"
      />
      <!-- Expand the split into the only-subagent takeover (full width). Not
           offered in roster mode (nothing scoped to maximize yet). -->
      <Button
        v-if="!isRoster"
        variant="ghost"
        size="icon"
        class="h-6 w-6 shrink-0 text-violet-500 hover:bg-violet-100 hover:text-violet-800"
        data-testid="trace-pane-expand"
        aria-label="Expand agent view"
        @click="emit('expand')"
      >
        <Icon name="maximize-2" :size="12" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        class="h-6 w-6 shrink-0 text-violet-500 hover:bg-violet-100 hover:text-violet-800"
        data-testid="trace-pane-exit"
        aria-label="Close agent pane"
        @click="emit('exit')"
      >
        <Icon name="x" :size="13" />
      </Button>
    </div>

    <!-- Roster mode: the running-first picker fills the pane. -->
    <div v-if="isRoster" class="p-1.5" data-testid="trace-agent-pane-roster">
      <TraceAgentRoster
        :running-agents="runningAgents"
        :finished-agents="finishedAgents"
        :server-now="serverNow"
        :server-now-at="serverNowAt"
        @pick="emit('scope', $event.agentId)"
      />
    </div>

    <!-- Unknown ?agent= id: terminal not-found, never a spinner. -->
    <div
      v-else-if="notFound"
      class="flex flex-col items-center gap-2 px-4 py-10 text-center text-slate-500"
      data-testid="trace-pane-notfound"
    >
      <span class="text-2xl text-rose-400" aria-hidden="true">?</span>
      <span class="text-[13px]">This agent isn’t in the session roster.</span>
      <Button
        variant="ghost"
        class="mt-1 h-auto rounded-md border border-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:border-slate-300 hover:text-slate-800"
        @click="emit('exit')"
      >Close pane</Button>
    </div>

    <!-- Deep-link limbo: roster not landed yet. -->
    <div
      v-else-if="pending"
      class="flex items-center justify-center gap-2 px-4 py-10 text-[13px] text-slate-400"
      data-testid="trace-pane-pending"
    >
      <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
        <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>
      Loading agent scope…
    </div>

    <!-- Scoped feed: the shared scoped projection (task prompt + subtree),
         with the fixed follow-latest pill suppressed (the main feed owns it)
         and the highlight signal cleared so the pane doesn't self-highlight. -->
    <div v-else class="px-3 py-3">
      <SessionConversationView
        :spans="spans"
        :turns="turns"
        :selected-span="selectedSpan"
        :trace-id="traceId"
        :context-window-tokens="contextWindowTokens"
        :workflow-runs-by-id="workflowRunsById"
        :loaded-subtrees="loadedSubtrees"
        :scope-agent="agent"
        :scope-loading="scopeLoading"
        :scoped-agent-id="''"
        :show-follow-tail="false"
        :scroller-getter="getPaneScroller"
        @select-span="emit('select-span', $event)"
        @fetch-content="emit('fetch-content', $event)"
        @load-subtree="emit('load-subtree', $event)"
        @jump-live="emit('jump-live')"
        @enter-scope="emit('scope', $event)"
      />
    </div>
  </aside>
</template>
