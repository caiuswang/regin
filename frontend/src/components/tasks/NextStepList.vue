<script setup>
// The Tasks tab's default reading: the open backlog grouped by what each task
// needs next — Ready to run, Running, Blocked, Failed, Ideas — every header
// counted by the server (`/api/tasks?group=next_step`), done counted and
// hidden. Each group pages on its own; Ideas opens on three.
import { computed, nextTick, ref, watch } from 'vue'
import api from '../../api'
import BulkRejected from './BulkRejected.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import NextStepRow from './NextStepRow.vue'
import RunAllReport from './RunAllReport.vue'
import TaskBulkBar from './TaskBulkBar.vue'
import TaskSheets from './TaskSheets.vue'
import { useConfirm } from '../../composables/useConfirm.js'
import { useFlash } from '../../composables/useFlash.js'
import { useProjects } from '../../composables/useProjects.js'
import { useTaskMutations } from '../../composables/useTasks.js'
import { useTaskSelection } from '../../composables/useTaskSelection.js'
import { useNextStep } from '../../composables/useTaskViews.js'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'

const props = defineProps({
  params: { type: Object, required: true },
  // A next-step group key a link arrived at (`?group=failed`): opened and
  // scrolled to once its rows are in.
  anchor: { type: String, default: '' },
})
const emit = defineEmits(['count'])

const params = computed(() => props.params)
const feed = useNextStep(params)
const { confirm } = useConfirm()
const { flash } = useFlash()
const collapsed = ref({})
const sheet = ref(null)
const runningAll = ref(false)
const runAllReport = ref(null)
const canWrite = api.getStoredUser()?.role !== 'viewer'
const selection = useTaskSelection()
const { bulk, bulkRemove } = useTaskMutations()
const { items: projects, ensure: ensureProjects } = useProjects()
const bulkRejected = ref([])

const groups = computed(() => (feed.body.value?.groups || []).filter(g => g.n > 0))
const empty = computed(() => feed.body.value && !groups.value.length)

async function reload() {
  await feed.load()
  const body = feed.body.value
  emit('count', body && !feed.error.value
    ? `${body.open} open · ${body.done_hidden} done hidden` : '')
}

async function afterWrite() {
  invalidatePipelineCounts()
  await reload()
}

const { refusals, busyId, act } = useTaskVerbs(afterWrite, {
  openSheet: (kind, task) => { sheet.value = { kind, task } },
})

function moreLabel(group) {
  return group.key === 'ideas'
    ? `${group.more} more ideas · sorted by priority`
    : `${group.more} more`
}

async function runAll(group) {
  const ok = await confirm('Run all ready tasks',
    `Start ${group.n} ${group.n === 1 ? 'run' : 'runs'} — every task in Ready to run?`)
  if (!ok) return
  runningAll.value = true
  runAllReport.value = null
  try {
    const ids = await feed.readyRows()
    const refused = []
    for (const { id, title } of ids) {
      const result = await api.post(`/tasks/${id}/run`)
      if (result.ok === false) {
        refused.push({ id, title, message: result.msg || 'the run was refused',
                       blocking: result.body?.blocking || [] })
      }
    }
    runAllReport.value = { started: ids.length - refused.length, refused }
    flash(refused.length
      ? `${ids.length - refused.length} started · ${refused.length} refused`
      : `${ids.length} ${ids.length === 1 ? 'run' : 'runs'} started`, refused.length ? 'error' : undefined)
  } finally {
    runningAll.value = false
    await afterWrite()
  }
}

function titleOf(id) {
  for (const group of feed.body.value?.groups || []) {
    const row = group.rows.find(r => r.id === id)
    if (row) return row.title
  }
  return ''
}

function nameRejected(result) {
  bulkRejected.value = (result.rejected || []).map(r => ({ ...r, title: titleOf(r.id) }))
}

async function applyBulk(fields) {
  const result = await bulk([...selection.ids.value], fields)
  if (!result) return
  nameRejected(result)
  selection.clear()
  await afterWrite()
}

async function deleteSelected() {
  const ids = [...selection.ids.value]
  const ok = await confirm('Delete tasks',
    `Delete ${ids.map(id => `#${id} “${titleOf(id)}”`).join(', ')} and their run `
    + 'history, images and links? This cannot be undone.', true)
  if (!ok) return
  const result = await bulkRemove(ids)
  if (!result) return
  nameRejected(result)
  selection.clear()
  await afterWrite()
}

function toggle(key) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] }
}

async function sheetDone() {
  sheet.value = null
  await afterWrite()
}

watch(() => JSON.stringify(props.params), () => {
  selection.clear()
  reload()
}, { immediate: true })
if (canWrite) ensureProjects()

// Once per arrival: a later reload must not yank the reader back to it.
let anchored = ''
watch(() => [props.anchor, groups.value.map(g => g.key).join()], async ([key]) => {
  if (!key) { anchored = ''; return }
  if (anchored === key || !groups.value.some(g => g.key === key)) return
  anchored = key
  collapsed.value = { ...collapsed.value, [key]: false }
  await nextTick()
  document.querySelector(`[data-testid="next-step-group-${key}"]`)
    ?.scrollIntoView({ block: 'start' })
}, { immediate: true })

defineExpose({ reload })
</script>

<template>
  <div class="nsl" data-testid="next-step-list">
    <TaskBulkBar
      v-if="selection.count.value"
      class="nsl-bar"
      :count="selection.count.value"
      :projects="projects"
      :restorable="false"
      @clear="selection.clear()"
      @archive="applyBulk({ status: 'archived' })"
      @delete="deleteSelected"
      @priority="v => applyBulk({ priority: v })"
      @project="v => applyBulk({ project_id: v === 'none' ? null : Number(v) })"
      @feature="id => applyBulk({ feature_id: id })"
    />
    <BulkRejected v-if="bulkRejected.length" :rows="bulkRejected" @dismiss="bulkRejected = []" />
    <p v-if="feed.error.value" class="nsl-note" role="alert" data-testid="next-step-error">
      Could not read the task list — {{ feed.error.value }}
      <Button variant="link" size="sm" @click="reload">Retry</Button>
    </p>
    <p v-else-if="!feed.body.value" class="nsl-note">Loading tasks…</p>
    <p v-else-if="empty" class="nsl-note" data-testid="next-step-empty">
      No open tasks match. Press <kbd>c</kbd> to capture one.
    </p>

    <section
      v-for="group in groups"
      :key="group.key"
      class="nsl-group"
      :class="{ 'is-anchored': group.key === anchor }"
      :data-anchored="group.key === anchor ? 'true' : undefined"
      :data-testid="`next-step-group-${group.key}`"
    >
      <header class="nsl-head">
        <Button
          variant="ghost"
          size="sm"
          class="nsl-toggle"
          :aria-expanded="!collapsed[group.key]"
          :data-testid="`next-step-toggle-${group.key}`"
          @click="toggle(group.key)"
        >
          <Icon :name="collapsed[group.key] ? 'chevron-right' : 'chevron-down'" :size="14" />
          <span class="nsl-label">{{ group.label }}</span>
          <span class="nsl-n" data-testid="next-step-n">{{ group.n }}</span>
        </Button>
        <span v-if="group.hint" class="nsl-hint">{{ group.hint }}</span>
        <Button
          v-if="group.key === 'ready' && canWrite"
          variant="link"
          size="sm"
          class="nsl-bulk"
          :disabled="runningAll"
          data-testid="next-step-run-all"
          @click="runAll(group)"
        >Run all {{ group.n }}</Button>
      </header>
      <RunAllReport
        v-if="group.key === 'ready' && runAllReport?.refused.length"
        :report="runAllReport"
        @dismiss="runAllReport = null"
      />
      <template v-if="!collapsed[group.key]">
        <ul class="nsl-rows">
          <NextStepRow
            v-for="task in group.rows"
            :key="task.id"
            :task="task"
            :busy="busyId === task.id || runningAll"
            :refusal="refusals[task.id] || null"
            :can-write="canWrite"
            :selected="selection.ids.value.has(task.id)"
            @verb="act"
            @select="selection.toggle"
          />
        </ul>
        <Button
          v-if="group.more > 0"
          variant="ghost"
          size="sm"
          class="nsl-more"
          :data-testid="`next-step-more-${group.key}`"
          @click="feed.more(group.key)"
        >{{ moreLabel(group) }}</Button>
      </template>
    </section>

    <TaskSheets :sheet="sheet" @close="sheet = null" @done="sheetDone" />
  </div>
</template>

<style scoped>
.nsl-bar { position: sticky; top: 0; z-index: 2; }
.nsl-note {
    padding: 1.25rem 0;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.nsl-group { scroll-margin-top: 0.5rem; }
.nsl-group.is-anchored .nsl-label { color: var(--color-primary); }
.nsl-head {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    padding: 0.625rem 0 0.25rem;
}
.nsl .nsl-toggle {
    gap: 0.625rem;
    margin-left: -0.5rem;
    padding-inline: 0.5rem;
    color: var(--color-fg);
}
.nsl-label { font-size: 0.8125rem; font-weight: 600; }
.nsl-n {
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.nsl-hint {
    overflow: hidden;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    text-overflow: ellipsis;
    white-space: nowrap;
}
.nsl .nsl-bulk {
    margin-left: auto;
    padding-inline: 0;
    font-size: 0.75rem;
    font-weight: 500;
}
.nsl-rows {
    margin: 0;
    padding: 0;
    list-style: none;
}
.nsl .nsl-more {
    width: 100%;
    justify-content: flex-start;
    padding-left: 3rem;
    font-size: 0.75rem;
    font-weight: 400;
    color: var(--color-fg-subtle);
    border-top: 1px solid var(--color-border-subtle);
    border-radius: 0;
}

@media (max-width: 767px) {
    .nsl-hint { display: none; }
    .nsl .nsl-bulk,
    .nsl .nsl-toggle { min-height: 2.75rem; }
    .nsl .nsl-bulk { font-size: 0.8125rem; font-weight: 600; }
    .nsl .nsl-more { min-height: 2.75rem; padding-left: 0; }
}
</style>
