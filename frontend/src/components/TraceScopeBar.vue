<script setup>
// Slim violet bar pinned with the sticky page header while the Conversation
// tab is scoped to one subagent's subtree — the desktop sibling of
// live/LiveScopeBar.vue, styled with the trace view's Tailwind idiom. The
// header above keeps showing MAIN-session truth; this bar is the scope's own
// signal. `agent` null + `notFound` renders the unknown-?agent= state.
import { computed } from 'vue'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import { agentStatusLabel } from '../utils/liveRows.js'
import { useAgentElapsed } from '../composables/useAgentElapsed.js'

const props = defineProps({
  // useLiveAgents roster entry, or null while unresolved / not found.
  agent: { type: Object, default: null },
  notFound: { type: Boolean, default: false },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})
const emit = defineEmits(['exit'])

const elapsed = useAgentElapsed(
  () => props.agent?.startTime || '',
  () => props.serverNow,
  () => props.serverNowAt,
  () => !!props.agent?.running,
)
// One status phrasing across every scoped-agent surface (see liveRows).
const status = computed(() => (props.agent
  ? agentStatusLabel(props.agent, elapsed.value)
  : ''))
</script>

<template>
  <div
    class="mt-3 flex items-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 py-1.5 text-[12px] text-violet-800"
    data-testid="trace-scope-bar"
  >
    <span class="text-violet-500" aria-hidden="true">◈</span>
    <template v-if="agent">
      <span class="font-semibold shrink-0">{{ agent.agentType }}</span>
      <span class="flex-1 min-w-0 truncate text-violet-700/80">{{ agent.description ? `— ${agent.description}` : '' }}</span>
      <span class="font-mono text-[11px] text-violet-600 shrink-0" data-testid="trace-scope-status">{{ status }}</span>
      <span class="text-violet-300" aria-hidden="true">·</span>
      <span class="font-mono text-[11px] text-violet-600 shrink-0">{{ agent.spanCount }} span<template v-if="agent.spanCount !== 1">s</template></span>
    </template>
    <span
      v-else-if="notFound"
      class="flex-1 min-w-0 text-violet-700/80"
      data-testid="trace-scope-notfound"
    >agent not found in this session</span>
    <span v-else class="flex-1 min-w-0 text-violet-700/80">loading agent…</span>
    <Button
      variant="ghost"
      size="icon"
      class="h-6 w-6 shrink-0 text-violet-500 hover:bg-violet-100 hover:text-violet-800"
      data-testid="trace-scope-exit"
      aria-label="Back to main view"
      @click="emit('exit')"
    >
      <Icon name="x" :size="13" />
    </Button>
  </div>
</template>
