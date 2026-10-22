<script setup>
// The runs holding `max_concurrent_runs` slots, each offering only what its
// phase allows (F167 Rules B): an idle session can be closed in place; one
// waiting on an answer or still working must be opened, because closing it
// would throw away a question or a turn; a run starting or stopping has
// nothing to take.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import { closeRun } from '../../composables/useRunSlots.js'
import { useFlash } from '../../composables/useFlash.js'

defineProps({
  holders: { type: Array, required: true },
  canWrite: { type: Boolean, default: true },
  closeLabel: { type: String, default: 'Close' },
  testid: { type: String, default: 'run-slot-list' },
})
const emit = defineEmits(['closed'])
const { flash } = useFlash()

const OPEN = new Set(['waiting-input', 'waiting-permission', 'working', 'retrying'])
const PHASE_LABEL = {
  'waiting-input': 'waiting for an answer',
  'waiting-permission': 'waiting for permission',
}

const closing = ref('')

async function close(holder) {
  closing.value = holder.trace_id
  const refused = await closeRun(holder.trace_id)
  closing.value = ''
  if (refused) {
    flash(refused, 'error')
    return
  }
  emit('closed', holder.trace_id)
}

const short = cwd => (cwd ? cwd.split('/').filter(Boolean).pop() : '')
</script>

<template>
  <ul class="rsl" :data-testid="testid">
    <li
      v-for="h in holders" :key="h.trace_id" class="rsl-row" data-pl-part="slot"
      :data-phase="h.phase" :data-testid="`run-slot-${h.trace_id}`"
    >
      <RouterLink
        v-if="h.task_id" class="rsl-what" data-pl-part="slot-what" :to="`/tasks/${h.task_id}`"
        data-testid="run-slot-task"
      >#{{ h.task_id }} {{ h.task_title }}</RouterLink>
      <span v-else class="rsl-what rsl-what--live" data-pl-part="slot-what" :title="h.cwd || ''">
        Live session{{ h.cwd ? ` · ${short(h.cwd)}` : '' }}
      </span>
      <span class="rsl-phase" data-pl-part="slot-phase" data-testid="run-slot-phase">{{ PHASE_LABEL[h.phase] || h.phase }}</span>
      <Button
        v-if="h.phase === 'idle' && canWrite"
        variant="secondary" size="sm" class="rsl-close" data-pl-part="key" data-tone="danger"
        :disabled="closing === h.trace_id"
        :aria-label="`${closeLabel} ${h.task_id ? `#${h.task_id}` : 'live session'}`"
        data-testid="run-slot-close"
        @click="close(h)"
      >{{ closeLabel }}</Button>
      <RouterLink
        v-else-if="OPEN.has(h.phase)" class="rsl-open" data-pl-part="key" :to="`/live/${h.trace_id}`"
        data-testid="run-slot-open"
      >Open</RouterLink>
    </li>
  </ul>
</template>

<style scoped>
.rsl { display: flex; flex-direction: column; gap: 0.25rem; margin: 0.375rem 0 0; padding: 0; list-style: none; }
.rsl-row { display: flex; align-items: center; gap: 0.5rem; min-width: 0; font-size: 0.75rem; }
.rsl-what {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--color-fg); text-decoration: none;
}
a.rsl-what:hover { text-decoration: underline; }
.rsl-what--live { color: var(--color-fg-muted); }
.rsl-phase { flex: none; color: var(--color-fg-muted); font-variant-numeric: tabular-nums; }
.rsl-close { flex: none; color: var(--color-danger); }
.rsl-open {
    flex: none; display: inline-flex; align-items: center; height: 1.75rem; padding: 0 0.625rem;
    font-weight: 600; color: var(--color-primary); text-decoration: none;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg);
}
.rsl-open:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
</style>
