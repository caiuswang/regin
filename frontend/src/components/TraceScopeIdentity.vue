<script setup>
// Agent identity block shared by the two scoped-state surfaces: the slim
// TraceScopeBar (<xl takeover) and the TraceAgentPane header (≥xl split).
// Renders the ◈ glyph, agent type, description, server status phrasing and
// span count — or the unknown-?agent= / still-loading fallbacks. The host
// supplies the flex container + close button; this is content only.
import { computed } from 'vue'
import { agentStatusLabel } from '../utils/liveRows.js'
import { useAgentElapsed } from '../composables/useAgentElapsed.js'

const props = defineProps({
  // useLiveAgents roster entry, or null while unresolved / not found.
  agent: { type: Object, default: null },
  notFound: { type: Boolean, default: false },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})

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
  <span class="text-violet-500 shrink-0" aria-hidden="true">◈</span>
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
</template>
