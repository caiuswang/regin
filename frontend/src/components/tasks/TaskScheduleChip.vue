<script setup>
// A task's schedule as one chip: "等待时间窗 · in 3h" when only the gate holds
// an armed, unblocked task; a clock and the next slot for a cron task; grey
// when the schedule is switched off.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import { scheduleChip } from '../../utils/taskSchedule.js'

const props = defineProps({
  task: { type: Object, required: true },
  // A lane node is fixed-width: the chip shrinks and truncates there.
  compact: { type: Boolean, default: false },
})

const chip = computed(() => scheduleChip(props.task))
</script>

<template>
  <span
    v-if="chip"
    class="tsc"
    :class="[`tsc-${chip.tone}`, { 'tsc-compact': compact }]"
    :title="chip.title"
    data-testid="task-schedule-chip"
  >
    <Icon name="clock" :size="11" />{{ chip.label }}
  </span>
</template>

<style scoped>
.tsc {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.0625rem 0.4375rem;
    font-size: 0.75rem;
    white-space: nowrap;
    border-radius: var(--radius-sm);
}
.tsc-compact {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 0.3125rem;
    font-size: 0.625rem;
}
.tsc-warn { color: var(--color-warning-strong); background: var(--color-warning-soft); }
.tsc-info { color: var(--color-fg-muted); background: var(--color-surface-2); }
.tsc-muted { color: var(--color-fg-faint); background: var(--color-surface-2); }
</style>
