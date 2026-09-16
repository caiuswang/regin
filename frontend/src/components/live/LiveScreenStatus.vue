<script setup>
// What the session is doing right now, plus the one-click way to go look.
//
// The gap this fills: while a tool is in flight the NOW zone already names
// it, but a session that is thinking or streaming emits no spans at all —
// its state is readable only from its live status. `screen` carries that for
// both tiers in one shape: the server's parse of the pane for a terminal
// session, the runner's own account for a run regin launched
// (lib/agent_bridge/screen_state.py).
//
// Rendered in EVERY zone state, not just the working one: "how do I know
// what this session is really doing" is a question the card has to answer
// from wherever it happens to be, so the status fields fill in whenever the
// source has them.
//
// A null `screen` means there was NO verdict (an unrecognised TUI, a pane in
// copy-mode) — never "idle". Saying so and pointing at the raw terminal is
// the honest render; inventing a state here would reintroduce exactly the
// bug this feature removes.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { fmtElapsedSeconds } from '../../utils/traceFormatters.js'

const props = defineProps({
  screen: { type: Object, default: null },
  // Empty when there is no pane behind this session: the bridge is off, it was
  // not launched under tmux, or regin launched it itself over the SDK. Gates
  // the chip alone — a run regin owns reports its status through `screen`
  // without a terminal to open, and the chip would lead nowhere.
  bridgePane: { type: String, default: '' },
  // False when the zone is already showing the pending tool itself — the
  // terminal's view of the same call is redundant there, the chip is not.
  showTool: { type: Boolean, default: true },
})
const emit = defineEmits(['open-terminal'])

const busy = computed(() => props.screen?.busy === true)

const elapsed = computed(() => {
  const s = props.screen?.elapsed_sec
  return typeof s === 'number' ? fmtElapsedSeconds(s) : ''
})

const tokens = computed(() => {
  const n = props.screen?.tokens
  if (typeof n !== 'number') return ''
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k tokens` : `${n} tokens`
})
</script>

<template>
  <div v-if="bridgePane || screen" class="live-screen" data-testid="live-screen">
    <div class="live-screen-1">
      <template v-if="busy">
        <span class="live-screen-verb">{{ screen.verb || 'working' }}…</span>
        <span v-if="tokens" class="live-screen-meta">{{ tokens }}</span>
        <span v-if="elapsed" class="live-screen-elapsed">{{ elapsed }}</span>
      </template>
      <span
        v-else-if="!screen"
        class="live-screen-meta"
        data-testid="live-screen-none"
      >status unavailable</span>
      <Button
        v-if="bridgePane"
        variant="ghost"
        size="sm"
        class="live-chip live-screen-peek"
        data-testid="live-screen-peek"
        @click="emit('open-terminal')"
      >
        <Icon name="terminal" :size="12" />
        terminal
      </Button>
    </div>
    <div v-if="busy && showTool && screen.tool" class="live-screen-tool">
      {{ screen.tool }}
    </div>
  </div>
</template>
