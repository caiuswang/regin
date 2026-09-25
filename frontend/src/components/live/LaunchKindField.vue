<script setup>
// Which kind of session the launch form starts: an SDK run regin holds the
// stream of, or an interactive `claude` on a pane regin owns, opened in the
// browser terminal. Both options always render — a kind this install or this
// user cannot use is disabled with the server's reason beside it, so nobody is
// left wondering where the Terminal option went.
import { computed } from 'vue'
import RadioGroup from '../ui/RadioGroup.vue'

const props = defineProps({
  opts: { type: Object, required: true },
})
const kind = defineModel({ type: String, default: 'sdk' })

const options = computed(() => [
  { value: 'sdk', label: 'SDK run', disabled: !props.opts.enabled },
  { value: 'terminal', label: 'Terminal', disabled: !props.opts.terminal_available },
])
</script>

<template>
  <div class="live-launch-kind" data-testid="live-launch-kind">
    <span class="live-launch-lbl">session kind</span>
    <RadioGroup v-model="kind" inline name="launch-kind" :options="options" />
    <p
      v-if="!opts.terminal_available"
      class="live-launch-kind-why"
      data-testid="live-launch-terminal-why"
    >
      terminal unavailable: {{ opts.terminal_detail }}
    </p>
    <p v-else-if="!opts.enabled" class="live-launch-kind-why">
      SDK run unavailable: agent_sdk disabled
    </p>
  </div>
</template>

<style scoped>
.live-launch-kind { display: flex; flex-direction: column; gap: 0.375rem; }
.live-launch-kind-why { margin: 0; font-size: 0.75rem; color: var(--color-fg-muted); }
</style>
