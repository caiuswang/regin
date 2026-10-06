<script setup>
// The Tasks tab as a board: Ideas, Armed, Running, Done — each column
// counted and paged by the server (`/api/tasks/board`). Failed is not a
// column: it is a sub-group heading Done, its cards behind "show", so a
// failure is read before the successes it would otherwise pass for. A column
// the server counts at 0 folds to a 48px rail so the columns holding cards get
// the row; Done stays open while it holds failed tasks, or they would be
// unreachable.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import TaskBoardCard from './TaskBoardCard.vue'
import TaskSheets from './TaskSheets.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'
import { useTaskBoard } from '../../composables/useTaskViews.js'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { statusGlyph } from '../../utils/taskMeta.js'

const props = defineProps({
  params: { type: Object, required: true },
})
const emit = defineEmits(['count', 'compose', 'view'])

const COLUMNS = {
  idea: { label: 'Ideas', hint: () => 'arm to run', noun: 'ideas' },
  armed: { label: 'Armed', hint: c => (c.blocked ? `${c.blocked} blocked` : ''), noun: 'armed' },
  running: { label: 'Running', hint: () => '', noun: 'running' },
  done: { label: 'Done', hint: () => 'newest first', noun: 'done' },
}

const params = computed(() => props.params)
const board = useTaskBoard(params)

const columns = computed(() => board.body.value?.columns || [])
const failedCount = computed(() => board.body.value?.failed || 0)

const QUEUE = ['armed', 'running']

function isRail(col) {
  return col.n === 0 && !(col.status === 'done' && failedCount.value > 0)
}

const queueClear = computed(() => columns.value.length > 0
  && columns.value.every(c => !QUEUE.includes(c.status) || c.n === 0))
const doneWeek = computed(() => board.body.value?.done_week || 0)

async function reload() {
  await board.load()
  const body = board.body.value
  if (!body || board.error.value) { emit('count', ''); return }
  const shown = body.columns.reduce((sum, c) => sum + c.n, 0) + body.failed
  emit('count', shown === body.population
    ? `${shown} ${shown === 1 ? 'task' : 'tasks'}`
    : `${shown} of ${body.population} tasks`)
}

const canWrite = api.getStoredUser()?.role !== 'viewer'
const sheet = ref(null)

async function afterWrite() {
  invalidatePipelineCounts()
  await reload()
}

const { refusals, busyId, act } = useTaskVerbs(afterWrite, {
  openSheet: (kind, task) => { sheet.value = { kind, task } },
})

async function sheetDone() {
  sheet.value = null
  await afterWrite()
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
    <template v-else>
      <div v-if="queueClear" class="tb-clear" role="status" data-pl-part="notice" data-testid="board-queue-clear">
        <Icon name="circle-check" :size="15" class="tb-clear-icon" />
        <span><b>Queue is clear.</b> Nothing armed or running — {{ doneWeek }}
          {{ doneWeek === 1 ? 'task' : 'tasks' }} done this week.</span>
        <span class="tb-clear-actions">
          <Button variant="secondary" size="sm" data-pl-part="key" @click="emit('compose')">New task</Button>
          <Button variant="link" size="sm" @click="emit('view', 'list')">Switch to List</Button>
        </span>
      </div>
      <div class="tb-cols">
        <template v-for="col in columns" :key="col.status">
          <section
            v-if="isRail(col)"
            class="tb-rail"
            data-kind="rail"
            data-pl-part="column"
            :data-testid="`board-column-${col.status}`"
            :aria-label="`${COLUMNS[col.status].label}: no tasks`"
          >
            <PlGlyph :kind="statusGlyph(col.status)" :label="col.status" :scale="2"><TaskStatusIcon :status="col.status" :size="14" /></PlGlyph>
            <span class="tb-rail-n" data-pl-part="count" data-testid="board-rail-n">0</span>
            <span class="tb-rail-label" data-pl-part="group-label" data-testid="board-rail-label">{{ COLUMNS[col.status].label }}</span>
          </section>
          <section
            v-else
            class="tb-col"
            data-kind="full"
            data-pl-part="column"
            :data-testid="`board-column-${col.status}`"
          >
            <header class="tb-head" data-pl-part="group-head" :data-pl-group="col.status">
              <PlGlyph :kind="statusGlyph(col.status)" :label="col.status" :scale="2"><TaskStatusIcon :status="col.status" :size="14" /></PlGlyph>
              <span class="tb-label" data-pl-part="group-label">{{ COLUMNS[col.status].label }}</span>
              <span class="tb-n" data-pl-part="count" data-testid="board-column-n">{{ col.n }}</span>
              <span class="tb-hint" data-pl-part="meta-label">{{ COLUMNS[col.status].hint(col) }}</span>
            </header>
            <div
              v-if="col.status === 'done' && failedCount"
              class="tb-failed"
              data-testid="board-failed-group"
            >
              <div class="tb-subhead" data-pl-part="group-head" data-pl-group="failed">
                <TaskStatusIcon status="failed" :size="12" />
                <span data-pl-part="group-label">Failed</span>
                <span class="tb-n" data-pl-part="count" data-testid="board-failed-n">{{ failedCount }}</span>
              </div>
              <div v-if="board.failed.value?.cards?.length" class="tb-cards">
                <TaskBoardCard
                  v-for="task in board.failed.value.cards" :key="task.id" :task="task"
                  :busy="busyId === task.id" :refusal="refusals[task.id] || null"
                  :can-write="canWrite" @verb="act"
                />
              </div>
              <Button
                v-if="!board.failed.value || board.failed.value.more > 0"
                variant="ghost"
                size="sm"
                class="tb-foot"
                data-pl-part="more"
                data-testid="board-failed-show"
                @click="board.showFailed()"
              >{{ board.failed.value ? `${board.failed.value.more} more failed` : `Show ${failedCount} failed` }}</Button>
            </div>
            <div v-if="col.cards.length" class="tb-cards">
              <TaskBoardCard
                v-for="task in col.cards" :key="task.id" :task="task"
                :busy="busyId === task.id" :refusal="refusals[task.id] || null"
                :can-write="canWrite" @verb="act"
              />
            </div>
            <Button
              v-if="col.more > 0"
              variant="ghost"
              size="sm"
              class="tb-foot"
              data-pl-part="more"
              :data-testid="`board-more-${col.status}`"
              @click="board.more(col.status)"
            >{{ col.more }} more {{ COLUMNS[col.status].noun }}</Button>
          </section>
        </template>
      </div>
    </template>
    <TaskSheets :sheet="sheet" @close="sheet = null" @done="sheetDone" />
  </div>
</template>

<style scoped>
.tb-note {
    padding: 1.25rem 0;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tb-clear {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
    padding: 0.625rem 0.875rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    background: var(--color-info-soft);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.tb-clear b { color: var(--color-fg); font-weight: 600; }
.tb-clear-icon { flex-shrink: 0; color: var(--color-primary); }
.tb-clear-actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.5rem;
    margin-left: auto;
}
.tb-cols {
    display: flex;
    gap: 0.75rem;
    min-height: 360px;
    overflow-x: auto;
    padding-bottom: 0.25rem;
}
.tb-col {
    display: flex;
    flex: 1 1 0;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 10rem;
}
.tb-cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(236px, 100%), 1fr));
    gap: 0.5rem;
    align-items: start;
}
.tb-rail {
    display: flex;
    flex: 0 0 48px;
    flex-direction: column;
    align-items: center;
    gap: 0.625rem;
    padding: 0.625rem 0;
    background: var(--color-surface);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
}
.tb-rail-n {
    font-size: 0.75rem;
    color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.tb-rail-label {
    writing-mode: vertical-rl;
    font-size: 0.78125rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--color-fg-subtle);
}
.tb-head {
    display: flex;
    align-items: center;
    gap: 0.4375rem;
    min-height: 2rem;
    padding: 0 0.25rem;
}
.tb-label { font-size: 0.875rem; font-weight: 600; }
.tb-failed {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--color-border-subtle);
}
.tb-subhead {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--color-danger);
}
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
