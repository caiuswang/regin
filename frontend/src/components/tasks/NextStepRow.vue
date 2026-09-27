<script setup>
// One row of the next-step list: priority, status, id, title over its feature
// line, the facts, the ONE action the row needs, and its day.
//
// The action is the server's (`row.action`): run, watch, arm or none. It is
// always visible — never revealed on hover — and a row waiting on a
// dependency is dimmed and offers nothing, because pressing Run there is a
// 409 the operator would have to read to learn what the row already knows.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskFacts from './TaskFacts.vue'
import TaskPriorityIcon from './TaskPriorityIcon.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { taskDay, taskSub } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  refusal: { type: Object, default: null },
})
const emit = defineEmits(['run', 'arm'])

const waiting = computed(() => !!props.task.waits_on)
const watchTo = computed(() => (props.task.live?.trace_id
  ? `/trace/sessions/${props.task.live.trace_id}` : `/tasks/${props.task.id}`))
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
      <span class="nsr-facts"><TaskFacts :task="task" /></span>
    </div>
    <div class="nsr-action">
      <Button
        v-if="task.action === 'run'"
        variant="secondary"
        size="sm"
        class="nsr-btn nsr-run"
        :disabled="busy"
        :aria-label="`Run ${task.title}`"
        data-testid="task-action-run"
        @click="emit('run', task)"
      >
        <Icon name="play" :size="12" class="nsr-play" />
        <span class="nsr-run-label">Run</span>
      </Button>
      <router-link
        v-else-if="task.action === 'watch'"
        :to="watchTo"
        class="nsr-btn nsr-watch"
        :aria-label="`Watch ${task.title}`"
        data-testid="task-action-watch"
      >Watch</router-link>
      <Button
        v-else-if="task.action === 'arm'"
        variant="secondary"
        size="sm"
        class="nsr-btn"
        :aria-label="`Arm ${task.title}`"
        data-testid="task-action-arm"
        @click="emit('arm', task)"
      >Arm</Button>
    </div>
    <span class="nsr-day">{{ taskDay(task.created_at) }}</span>
    <p v-if="refusal" class="nsr-refusal" role="alert">{{ refusal.message }}</p>
  </li>
</template>

<style scoped>
.nsr {
    display: grid;
    grid-template-columns: 1.25rem 1.25rem 3rem minmax(0, 1fr) auto 4.75rem 3.5rem;
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
    justify-content: flex-end;
}
.nsr-day {
    grid-area: day;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    text-align: right;
    white-space: nowrap;
}
.nsr .nsr-run {
    color: var(--color-primary);
    background: var(--color-info-soft);
}
.nsr-watch {
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
.nsr-watch:hover { text-decoration: none; filter: brightness(0.97); }
.nsr-watch:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.nsr-play { fill: currentColor; }
.nsr.is-waiting { opacity: 0.65; }
.nsr-refusal {
    grid-column: main / -1;
    font-size: 0.75rem;
    color: var(--color-danger);
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
    .nsr-run-label { display: none; }
    .nsr .nsr-btn {
        min-width: 2.75rem;
        min-height: 2.75rem;
        justify-content: center;
    }
}
</style>
