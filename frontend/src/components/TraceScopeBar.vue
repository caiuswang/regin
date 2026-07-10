<script setup>
// Slim violet bar pinned with the sticky page header while the Conversation
// tab is scoped to one subagent's subtree in <xl TAKEOVER mode — the desktop
// sibling of live/LiveScopeBar.vue. At ≥xl the scope renders as TraceAgentPane
// instead and this bar is not shown. The header above keeps showing
// MAIN-session truth; this bar is the scope's own signal. The agent identity
// (glyph/type/desc/status/count/not-found) is the shared TraceScopeIdentity.
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import TraceScopeIdentity from './TraceScopeIdentity.vue'

defineProps({
  // useLiveAgents roster entry, or null while unresolved / not found.
  agent: { type: Object, default: null },
  notFound: { type: Boolean, default: false },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
  // ≥xl only: this takeover is the user-chosen 'full' presentation of a scope
  // that could instead be the split pane — offer a collapse-back control.
  canCollapse: { type: Boolean, default: false },
})
const emit = defineEmits(['exit', 'collapse'])
</script>

<template>
  <div
    class="mt-3 flex items-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 py-1.5 text-[12px] text-violet-800"
    data-testid="trace-scope-bar"
  >
    <TraceScopeIdentity
      :agent="agent"
      :not-found="notFound"
      :server-now="serverNow"
      :server-now-at="serverNowAt"
    />
    <Button
      v-if="canCollapse"
      variant="ghost"
      size="icon"
      class="h-6 w-6 shrink-0 text-violet-500 hover:bg-violet-100 hover:text-violet-800"
      data-testid="trace-scope-collapse"
      aria-label="Back to split view"
      @click="emit('collapse')"
    >
      <Icon name="minimize-2" :size="12" />
    </Button>
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
