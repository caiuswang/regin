<script setup>
// A board card: priority and id, the live run or the attempt count, the title,
// its feature, and what it fixes or waits on. The whole card opens the task.
// A card waiting on a dependency is dimmed; a running one wears the agent ring.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import TaskFacts from './TaskFacts.vue'
import TaskPriorityIcon from './TaskPriorityIcon.vue'
import { liveFor } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
})

const corner = computed(() => {
  const t = props.task
  if (t.live) return `live · ${liveFor(t.live.started_at)}`
  if ((t.status === 'done' || t.status === 'failed') && t.run_count) {
    return `${t.run_count} ${t.run_count === 1 ? 'attempt' : 'attempts'}`
  }
  return ''
})
</script>

<template>
  <router-link
    :to="`/tasks/${task.id}`"
    class="tbc"
    :class="{ 'is-waiting': !!task.waits_on, 'is-live': !!task.live }"
    :data-testid="`board-card-${task.id}`"
  >
    <span class="tbc-top">
      <TaskPriorityIcon :value="task.priority" :size="13" />
      <span class="tbc-id">#{{ task.id }}</span>
      <span v-if="corner" class="tbc-corner">
        <span v-if="task.live" class="tbc-dot" aria-hidden="true" />{{ corner }}
      </span>
    </span>
    <span class="tbc-title">{{ task.title }}</span>
    <span v-if="task.feature_id != null" class="tbc-feature">
      <Icon name="flag" :size="11" />#{{ task.feature_id }} {{ task.feature_title }}
    </span>
    <span v-if="task.fixes_defects?.length || task.waits_on" class="tbc-facts">
      <TaskFacts :task="task" :show-live="false" />
    </span>
  </router-link>
</template>

<style scoped>
.tbc {
    display: flex;
    flex-direction: column;
    gap: 0.4375rem;
    padding: 0.625rem 0.75rem 0.75rem;
    color: var(--color-fg);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.tbc:hover { text-decoration: none; border-color: var(--color-border-strong); }
.tbc:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.tbc.is-waiting { opacity: 0.65; }
.tbc.is-live {
    border-color: var(--color-agent);
    box-shadow: 0 0 0 2px var(--color-agent-line);
}
.tbc-top {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}
.tbc-id {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
}
.tbc-corner {
    display: inline-flex;
    align-items: center;
    gap: 0.3125rem;
    margin-left: auto;
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.is-live .tbc-corner { font-weight: 600; color: var(--color-agent); }
.tbc-dot {
    width: 0.375rem;
    height: 0.375rem;
    background: currentColor;
    border-radius: 999px;
}
.tbc-title {
    font-size: 0.8125rem;
    font-weight: 600;
    line-height: 1.35;
    overflow-wrap: anywhere;
}
.tbc-feature {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    gap: 0.3125rem;
    max-width: 100%;
    padding: 0.0625rem 0.4375rem;
    overflow: hidden;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
    white-space: nowrap;
    text-overflow: ellipsis;
    background: var(--color-surface-2);
    border-radius: var(--radius-sm);
}
.tbc-facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3125rem;
}
</style>
