<script setup>
// Slim violet bar pinned under the header while the tail is scoped to one
// subagent's span space. The bar is the ONLY scoped-state chrome —
// the tail itself stays untinted. "◈ <agent_type> — <description>" +
// running/finished status + a ✕ back to main. The header above keeps showing
// MAIN-session truth (status dot, chips); this bar is the scope's own signal.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { agentStatusLabel } from '../../utils/liveRows.js'
import { useAgentElapsed } from '../../composables/useAgentElapsed.js'

const props = defineProps({
  agent: { type: Object, required: true },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})
const emit = defineEmits(['exit'])

const elapsed = useAgentElapsed(
  () => props.agent.startTime,
  () => props.serverNow,
  () => props.serverNowAt,
  () => props.agent.running,
)
// Shared phrasing with the scoped NOW zone and the agents sheet: running ·
// elapsed / finished · duration / interrupted / stale · last seen HH:MM.
const status = computed(() => agentStatusLabel(props.agent, elapsed.value))
</script>

<template>
  <div class="live-scope-bar" data-testid="live-scope-bar">
    <span class="live-scope-glyph" aria-hidden="true">◈</span>
    <span class="live-scope-name">{{ agent.agentType }}</span>
    <!-- Keep the element even without a description — it is the flex:1
         filler pushing the status right; only the separator is conditional
         (an orphan agent would otherwise read "agent— "). -->
    <span class="live-scope-desc">{{ agent.description ? `— ${agent.description}` : '' }}</span>
    <span class="live-scope-status live-tabnum">{{ status }}</span>
    <Button
      variant="ghost"
      size="icon"
      class="live-scope-x"
      data-testid="live-scope-exit"
      aria-label="Back to main scope"
      @click="emit('exit')"
    >
      <Icon name="x" :size="13" />
    </Button>
  </div>
</template>
