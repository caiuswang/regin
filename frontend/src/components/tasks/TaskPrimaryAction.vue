<script setup>
// The one visible action a task row offers, by state (feature #119 R4a):
// Arm · ▶ Run · Retry · Watch and Stop · "waits on #N". The state → action
// mapping is the server's (`task.action`); this only draws it, identically on
// the list row, the board card, the graph node, the detail page and Overview.
//
// A `running` row whose run the liveness probe answered as gone offers
// "Run again" instead of Stop: that is what a runner killed with its server
// leaves, and Stop there has nothing to deliver to.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { waitsLabel } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  liveness: { type: Object, default: null },
  canWrite: { type: Boolean, default: true },
  // Icon-only buttons, for a box too small for labels (a graph node).
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['verb'])

const waitsOn = computed(() => {
  const w = props.task.waits_on
  return w && typeof w === 'object' ? w.id : w ?? null
})
const waitsText = computed(() => (typeof props.task.waits_on === 'object'
  ? waitsLabel(props.task) : `waits on #${waitsOn.value}`))
const stale = computed(() => props.task.action === 'watch'
  && props.liveness != null && !props.liveness.live)
// A run the server has opened but whose session has not announced itself yet:
// there is nothing to watch and nothing a Stop could reach until the next
// `pipeline` event brings the trace id.
const starting = computed(() => props.task.action === 'watch' && !props.task.live?.trace_id)
const watchTo = computed(() => `/trace/sessions/${props.task.live?.trace_id}`)
const ref_ = computed(() => `#${props.task.id}`)
const RUN_LABEL = { run: 'Run', retry: 'Retry' }
</script>

<template>
  <span class="tpa" :class="{ 'is-compact': compact }" :data-testid="`task-primary-${task.id}`">
    <template v-if="task.action === 'watch'">
      <span
        v-if="starting"
        class="tpa-watch is-starting"
        data-pl-part="key"
        role="status"
        data-testid="task-action-starting"
      >Starting…</span>
      <router-link
        v-else
        :to="watchTo"
        class="tpa-watch"
        data-pl-part="key"
        :aria-label="`Watch ${ref_}`"
        data-testid="task-action-watch"
        @click.stop
      >Watch</router-link>
      <Button
        v-if="canWrite && stale && !starting"
        variant="secondary"
        size="sm"
        :disabled="busy"
        data-pl-part="key"
        :aria-label="`Run ${ref_} again`"
        data-testid="task-action-run"
        @click.stop.prevent="emit('verb', 'run')"
      >Run again</Button>
      <Button
        v-else-if="canWrite"
        variant="danger"
        size="sm"
        :disabled="busy || starting"
        data-pl-part="key"
        :aria-label="`Stop ${ref_}`"
        data-testid="task-action-stop"
        @click.stop.prevent="emit('verb', 'stop')"
      >
        <Icon name="square" :size="11" />
        <span class="tpa-label">Stop</span>
      </Button>
    </template>
    <Button
      v-else-if="canWrite && RUN_LABEL[task.action]"
      variant="secondary"
      size="sm"
      class="tpa-run"
      data-pl-part="key"
      :disabled="busy"
      :aria-label="`${RUN_LABEL[task.action]} ${ref_}`"
      :data-testid="`task-action-${task.action}`"
      @click.stop.prevent="emit('verb', task.action)"
    >
      <Icon :name="task.action === 'run' ? 'play' : 'refresh-cw'" :size="12" class="tpa-icon" />
      <span class="tpa-label">{{ RUN_LABEL[task.action] }}</span>
    </Button>
    <Button
      v-else-if="canWrite && task.action === 'arm'"
      variant="secondary"
      size="sm"
      :disabled="busy"
      data-pl-part="key"
      :aria-label="`Arm ${ref_}`"
      data-testid="task-action-arm"
      @click.stop.prevent="emit('verb', 'arm')"
    >Arm</Button>
    <router-link
      v-else-if="waitsOn != null"
      :to="`/tasks/${waitsOn}`"
      class="tpa-waits"
      data-pl-part="meta-label"
      data-testid="task-action-waits"
      @click.stop
    >{{ waitsText }}</router-link>
  </span>
</template>

<style scoped>
.tpa {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
}
.tpa-watch {
    display: inline-flex;
    align-items: center;
    height: 1.75rem;
    padding: 0 0.625rem;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-agent);
    background: var(--color-agent-soft);
    border-radius: var(--radius-lg);
}
.tpa-watch:hover { text-decoration: none; filter: brightness(0.97); }
.tpa-watch.is-starting { color: var(--color-fg-muted); background: var(--color-surface-2); }
.tpa-watch.is-starting:hover { filter: none; }
.tpa-watch:focus-visible,
.tpa-waits:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.tpa .tpa-run {
    color: var(--color-primary);
    background: var(--color-info-soft);
}
.tpa-icon { fill: currentColor; }
.tpa.is-compact .tpa-label { display: none; }
.tpa-waits {
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    white-space: nowrap;
    text-decoration: underline;
    text-underline-offset: 2px;
}
.tpa-waits:hover { color: var(--color-fg); }

@media (max-width: 767px) {
    .tpa .tpa-label { display: none; }
    .tpa :deep(button),
    .tpa-watch {
        min-width: 2.75rem;
        min-height: 2.75rem;
        justify-content: center;
    }
}
</style>
