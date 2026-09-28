<script setup>
// A board card: priority and id, the live run or the attempt count, the title,
// its feature, what it fixes or waits on, and the row's primary action and ⋯
// menu (feature #119 R4a). The whole card still opens the task: the title's
// link is stretched over the card, and the controls sit above it — a button
// inside an anchor is not a button a keyboard or a screen reader can reach.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import TaskFacts from './TaskFacts.vue'
import TaskPrimaryAction from './TaskPrimaryAction.vue'
import TaskPriorityIcon from './TaskPriorityIcon.vue'
import TaskRefusal from './TaskRefusal.vue'
import TaskVerbMenu from './TaskVerbMenu.vue'
import { liveFor } from '../../utils/taskMeta.js'

const props = defineProps({
  task: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  refusal: { type: Object, default: null },
  canWrite: { type: Boolean, default: true },
})
const emit = defineEmits(['verb'])

const corner = computed(() => {
  const t = props.task
  if (t.live) return `live · ${liveFor(t.live.started_at)}`
  if ((t.status === 'done' || t.status === 'failed') && t.run_count) {
    return `${t.run_count} ${t.run_count === 1 ? 'attempt' : 'attempts'}`
  }
  return ''
})
const hasControls = computed(() => !!props.task.action || !!props.task.waits_on
  || (props.canWrite && (props.task.menu || []).length > 0))
</script>

<template>
  <div
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
    <router-link :to="`/tasks/${task.id}`" class="tbc-title">{{ task.title }}</router-link>
    <span v-if="task.feature_id != null" class="tbc-feature">
      <Icon name="flag" :size="11" />#{{ task.feature_id }} {{ task.feature_title }}
    </span>
    <span v-if="task.fixes_defects?.length" class="tbc-facts">
      <TaskFacts :task="task" :show-live="false" :show-waits="false" />
    </span>
    <span v-if="hasControls" class="tbc-actions">
      <TaskPrimaryAction
        :task="task" :busy="busy" :can-write="canWrite"
        @verb="v => emit('verb', v, task)"
      />
      <TaskVerbMenu
        v-if="canWrite" class="tbc-menu" :task="task" :busy="busy"
        @verb="v => emit('verb', v, task)"
      />
    </span>
    <TaskRefusal v-if="refusal" class="tbc-refusal" :refusal="refusal" />
  </div>
</template>

<style scoped>
.tbc {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 0.4375rem;
    padding: 0.625rem 0.75rem 0.75rem;
    color: var(--color-fg);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.tbc:hover { border-color: var(--color-border-strong); }
.tbc:focus-within { outline: 2px solid var(--color-ring); outline-offset: 2px; }
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
    color: var(--color-fg);
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
.tbc-title:hover { text-decoration: none; }
.tbc-title:focus-visible { outline: none; }
.tbc-title::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: inherit;
}
.tbc-actions,
.tbc :deep(.task-refusal) {
    position: relative;
    z-index: 1;
}
.tbc-actions {
    display: flex;
    align-items: center;
    gap: 0.25rem;
}
.tbc-actions .tbc-menu { margin-left: auto; }
.tbc .tbc-refusal { margin-top: 0; }
</style>
