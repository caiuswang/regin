<script setup>
// The card kind for a span that has not reported back yet. It short-circuits
// the ordinary card chain rather than living inside it, because every branch
// of that chain is written around a RESULT: an in-flight Bash renders an empty
// terminal panel, an in-flight Edit an empty diff, and an empty output panel
// reads as "finished with no output" — the opposite of what is true.
//
// So: the pill, what is being run, and a ticking clock. No output panel, no
// token count, no duration — duration is only written when the span closes.
import { computed } from 'vue'
import { useAgentElapsed } from '../../../composables/useAgentElapsed.js'
import { fullLabel, toolDisplayLabel } from '../../../utils/traceFormatters.js'

const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])

// No server clock reaches a conversation card, so this is the phone-clock
// branch of the shared formula — the same one every other elapsed readout in
// the app uses, rather than a second definition of "how long has this run".
const elapsed = useAgentElapsed(
  () => props.span.start_time, () => null, () => null, () => true,
)

const kind = computed(() => {
  const name = props.span.name || ''
  if (name === 'tool.Bash') return 'SHELL'
  if (name.startsWith('tool.')) return toolDisplayLabel(name.slice(5)).toUpperCase()
  if (name === 'assistant.thinking') return 'THINKING'
  return (name.split('.').pop() || 'SPAN').toUpperCase()
})

// The command as typed beats a rendered label: for the one span still moving,
// the reader wants to know exactly what is running.
const detail = computed(() => props.span.attributes?.command || fullLabel(props.span))

const selected = computed(() => props.selectedSpan?.span_id === props.span.span_id)
</script>

<template>
  <div
    class="running-card cursor-pointer transition-colors hover:border-emerald-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-ring)]"
    :class="selected ? 'event-selected' : ''"
    data-testid="running-span-card"
    role="button"
    tabindex="0"
    @click="$emit('activate', span)"
    @keydown.enter.prevent="$emit('activate', span)"
    @keydown.space.prevent="$emit('activate', span)"
  >
    <div class="flex items-center gap-2">
      <span class="running-card-pill">
        <span class="span-running-pip" aria-hidden="true"></span>running
      </span>
      <span class="flex-1"></span>
      <span v-if="elapsed" class="font-mono text-[11px] text-success" data-testid="running-elapsed">{{ elapsed }}</span>
    </div>
    <div class="mt-[7px] flex items-baseline gap-2">
      <span class="shrink-0 text-[11px] font-bold text-fg-muted">{{ kind }}</span>
      <code class="min-w-0 flex-1 truncate font-mono text-[11.5px] text-fg">{{ detail }}</code>
    </div>
  </div>
</template>

<style scoped>
.running-card {
  border-radius: 10px;
  border: 1px solid var(--color-emerald-200);
  background: var(--color-emerald-50);
  padding: 9px 12px;
  cursor: pointer;
}
.running-card-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 9999px;
  border: 1px solid var(--color-emerald-200);
  background: var(--color-surface);
  padding: 2px 9px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--color-success-strong);
}
</style>
