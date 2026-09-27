<script setup>
// The Tasks tab as a board: Ideas, Armed, Queued, Running, Done — each column
// counted and paged by the server (`/api/tasks/board`). Failed is not a
// column: it is a count under Done that opens on "show".
import { computed, watch } from 'vue'
import Button from '../ui/Button.vue'
import TaskBoardCard from './TaskBoardCard.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { useTaskBoard } from '../../composables/useTaskViews.js'

const props = defineProps({
  params: { type: Object, required: true },
})
const emit = defineEmits(['count'])

const COLUMNS = {
  idea: { label: 'Ideas', hint: () => 'arm to run', noun: 'ideas' },
  armed: { label: 'Armed', hint: c => (c.blocked ? `${c.blocked} blocked` : ''), noun: 'armed' },
  queued: { label: 'Queued', hint: () => 'next up', noun: 'queued' },
  running: { label: 'Running', hint: () => '', noun: 'running' },
  done: { label: 'Done', hint: () => 'newest first', noun: 'done' },
}

const params = computed(() => props.params)
const board = useTaskBoard(params)

const columns = computed(() => board.body.value?.columns || [])
const failedCount = computed(() => board.body.value?.failed || 0)

async function reload() {
  await board.load()
  const body = board.body.value
  if (!body || board.error.value) { emit('count', ''); return }
  const shown = body.columns.reduce((sum, c) => sum + c.n, 0) + body.failed
  emit('count', shown === body.population
    ? `${shown} ${shown === 1 ? 'task' : 'tasks'}`
    : `${shown} of ${body.population} tasks`)
}

watch(() => JSON.stringify(props.params), reload, { immediate: true })

defineExpose({ reload })
</script>

<template>
  <div class="tb" data-testid="task-board">
    <p v-if="board.error.value" class="tb-note" role="alert">
      Could not read the board — {{ board.error.value }}
      <Button variant="link" size="sm" @click="reload">Retry</Button>
    </p>
    <p v-else-if="!board.body.value" class="tb-note">Loading the board…</p>
    <div v-else class="tb-cols">
      <section
        v-for="col in columns"
        :key="col.status"
        class="tb-col"
        :data-testid="`board-column-${col.status}`"
      >
        <header class="tb-head">
          <TaskStatusIcon :status="col.status" :size="14" />
          <span class="tb-label">{{ COLUMNS[col.status].label }}</span>
          <span class="tb-n" data-testid="board-column-n">{{ col.n }}</span>
          <span class="tb-hint">{{ COLUMNS[col.status].hint(col) }}</span>
        </header>
        <TaskBoardCard v-for="task in col.cards" :key="task.id" :task="task" />
        <Button
          v-if="col.more > 0"
          variant="ghost"
          size="sm"
          class="tb-foot"
          :data-testid="`board-more-${col.status}`"
          @click="board.more(col.status)"
        >{{ col.more }} more {{ COLUMNS[col.status].noun }}</Button>
        <template v-if="col.status === 'done' && failedCount">
          <TaskBoardCard v-for="task in board.failed.value?.cards || []" :key="task.id" :task="task" />
          <Button
            v-if="!board.failed.value || board.failed.value.more > 0"
            variant="ghost"
            size="sm"
            class="tb-foot"
            data-testid="board-failed-show"
            @click="board.showFailed()"
          >+ {{ board.failed.value ? board.failed.value.more : failedCount }} failed · show</Button>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tb-note {
    padding: 1.25rem 0;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tb-cols {
    display: grid;
    grid-template-columns: repeat(5, minmax(12rem, 1fr));
    gap: 0.75rem;
    overflow-x: auto;
    padding-bottom: 0.25rem;
}
.tb-col {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 0;
}
.tb-head {
    display: flex;
    align-items: center;
    gap: 0.4375rem;
    min-height: 2rem;
    padding: 0 0.25rem;
}
.tb-label { font-size: 0.875rem; font-weight: 600; }
.tb-n {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.tb-hint {
    margin-left: auto;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    white-space: nowrap;
}
.tb .tb-foot {
    width: 100%;
    border: 1px dashed var(--color-border-strong);
}
</style>
