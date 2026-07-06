<script setup>
// Curated agent-detail sheet body, sourced purely from the ROSTER entry
// (never the loaded tail) — the prompt text, start clock, and status/
// duration all resolve even when the agent's start span sits outside the
// paginated window. Opened from the chevron on every agents-panel row.
import { computed } from 'vue'
import { agentStatusLabel } from '../../utils/liveRows.js'
import { useAgentElapsed } from '../../composables/useAgentElapsed.js'

const props = defineProps({
  agent: { type: Object, default: null },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})

const elapsed = useAgentElapsed(
  () => props.agent?.startTime,
  () => props.serverNow,
  () => props.serverNowAt,
  () => props.agent?.running,
)

const statusLine = computed(() => (props.agent
  ? agentStatusLabel(props.agent, elapsed.value)
  : ''))
</script>

<template>
  <div v-if="agent" class="live-agent-detail" data-testid="live-agent-detail">
    <dl class="live-attrs">
      <dt>agent</dt>
      <dd>{{ agent.agentType }}</dd>
      <dt>started</dt>
      <dd>{{ agent.startClock || '—' }}</dd>
      <dt>status</dt>
      <dd>{{ statusLine }}</dd>
    </dl>
    <p
      class="live-agent-detail-prompt"
      :class="{ 'live-agent-detail-empty': !agent.promptPreview }"
    >{{ agent.promptPreview || 'no prompt captured' }}</p>
  </div>
</template>
