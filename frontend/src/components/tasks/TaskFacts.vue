<script setup>
// The small facts a task row or card carries beside its title: the defects
// it fixes, the dependency it waits on, its schedule, and its live run. Shared by the
// grouped list and the board so the two readings of one task say the same.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import TaskScheduleChip from './TaskScheduleChip.vue'
import { liveFor, waitsLabel } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  showLive: { type: Boolean, default: true },
  // Off where the row's primary action already is the waits-on link.
  showWaits: { type: Boolean, default: true },
})

const fixes = computed(() => props.task.fixes_defects || [])
const waits = computed(() => (props.showWaits ? waitsLabel(props.task) : ''))
const live = computed(() => (props.showLive && props.task.live
  ? `live · ${liveFor(props.task.live.started_at)}` : ''))
</script>

<template>
  <span
    v-for="id in fixes"
    :key="`fix-${id}`"
    class="tf-chip tf-fix"
    data-pl-part="tag"
    data-tone="fail"
    data-testid="task-fixes"
  >fixes D-{{ id }}</span>
  <span v-if="waits" class="tf-chip tf-wait" data-pl-part="tag" data-tone="warn" data-testid="task-waits">
    <Icon name="dependency-tree" :size="11" />{{ waits }}
  </span>
  <TaskScheduleChip :task="task" />
  <span v-if="live" class="tf-live" data-pl-part="live" data-testid="task-live">
    <span class="tf-dot" aria-hidden="true" />{{ live }}
  </span>
</template>

<style scoped>
.tf-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.0625rem 0.4375rem;
    font-size: 0.75rem;
    white-space: nowrap;
    border-radius: var(--radius-sm);
}
.tf-fix {
    color: var(--color-danger);
    background: var(--color-danger-soft);
}
.tf-wait {
    color: var(--color-warning-strong);
    background: var(--color-warning-soft);
}
.tf-live {
    display: inline-flex;
    align-items: center;
    gap: 0.3125rem;
    font-size: 0.75rem;
    font-weight: 600;
    white-space: nowrap;
    color: var(--color-agent);
}
.tf-dot {
    width: 0.4375rem;
    height: 0.4375rem;
    background: currentColor;
    border-radius: 999px;
    box-shadow: 0 0 0 3px var(--color-agent-soft);
}
</style>
