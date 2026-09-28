<script setup>
// One row of the next-step list: priority, status, id, title over its feature
// line, the facts, the row's primary action and ⋯ menu, and its day.
//
// Both come from the server (`row.action`, `row.menu` — feature #119 R4a) and
// are always visible, never revealed on hover. A row waiting on a dependency
// is dimmed and its primary is the link to what it waits on.
import { computed } from 'vue'
import TaskFacts from './TaskFacts.vue'
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskPriorityIcon from './TaskPriorityIcon.vue'
import TaskRefusal from './TaskRefusal.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import TaskVerbMenu from './TaskVerbMenu.vue'
import { taskDay, taskSub } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  refusal: { type: Object, default: null },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['verb'])

const waiting = computed(() => !!props.task.waits_on)
</script>

<template>
  <li
    :id="`task-${task.id}`"
    class="nsr"
    :class="{ 'is-waiting': waiting }"
    :data-testid="`next-step-row-${task.id}`"
  >
    <TaskPriorityIcon class="nsr-prio" :value="task.priority" />
    <TaskStatusIcon class="nsr-status" :status="task.status" />
    <div class="nsr-main">
      <router-link :to="`/tasks/${task.id}`" class="nsr-title">{{ task.title }}</router-link>
      <span class="nsr-sub">{{ taskSub(task) }}</span>
    </div>
    <div class="nsr-meta">
      <span class="nsr-id">#{{ task.id }}</span>
      <span class="nsr-facts"><TaskFacts :task="task" :show-waits="false" /></span>
    </div>
    <div class="nsr-action">
      <TaskPrimaryAction
        :task="task"
        :busy="busy"
        :can-write="canWrite"
        @verb="v => emit('verb', v, task)"
      />
      <TaskVerbMenu v-if="canWrite" :task="task" :busy="busy" @verb="v => emit('verb', v, task)" />
    </div>
    <span class="nsr-day">{{ taskDay(task.created_at) }}</span>
    <TaskRefusal v-if="refusal" class="nsr-refusal" :refusal="refusal" />
  </li>
</template>

<style scoped>
.nsr {
    display: grid;
    grid-template-columns: 1.25rem 1.25rem 3rem minmax(0, 1fr) auto auto 3.5rem;
    grid-template-areas: "prio status id main facts action day";
    align-items: center;
    column-gap: 0.625rem;
    min-height: 3.25rem;
    padding: 0.4375rem 0;
    border-top: 1px solid var(--color-border-subtle);
}
.nsr-prio { grid-area: prio; }
.nsr-status { grid-area: status; }
.nsr-main {
    grid-area: main;
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.nsr-meta { display: contents; }
.nsr-id {
    grid-area: id;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem;
    color: var(--color-fg-subtle);
}
.nsr-facts {
    grid-area: facts;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: 0.375rem;
}
.nsr-title {
    overflow: hidden;
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--color-fg);
    text-overflow: ellipsis;
    white-space: nowrap;
}
.nsr-title:hover { color: var(--color-primary); text-decoration: none; }
.nsr-title:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.nsr-sub {
    overflow: hidden;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    text-overflow: ellipsis;
    white-space: nowrap;
}
.nsr-action {
    grid-area: action;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.25rem;
    min-width: 4.75rem;
}
.nsr-day {
    grid-area: day;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    text-align: right;
    white-space: nowrap;
}
.nsr.is-waiting { opacity: 0.65; }
.nsr .nsr-refusal {
    grid-column: main / -1;
    margin-top: 0.25rem;
}

@media (max-width: 767px) {
    .nsr {
        grid-template-columns: 1.25rem minmax(0, 1fr) auto;
        grid-template-areas: "status main action" "status meta action";
        column-gap: 0.75rem;
        row-gap: 0.125rem;
        padding: 0.75rem 0;
    }
    .nsr-prio,
    .nsr-sub,
    .nsr-day { display: none; }
    .nsr-status { align-self: start; margin-top: 0.1875rem; }
    .nsr-title { white-space: normal; font-size: 0.9375rem; }
    .nsr-meta {
        grid-area: meta;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.375rem;
    }
    .nsr-facts { justify-content: flex-start; }
}
</style>
