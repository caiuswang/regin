<script setup>
// The /tasks list surface: filter, group, select, act.
//
// Every refresh here is caused by something — a mount, a filter change, a
// mutation that resolved, a capture announced on the window — never by a
// timer. And every filter is a query parameter, so what the counts say and
// what the list pages through are the same set.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import TaskArmSheet from './TaskArmSheet.vue'
import TaskBulkBar from './TaskBulkBar.vue'
import TaskComposerDialog from './TaskComposerDialog.vue'
import TaskDepsEditor from './TaskDepsEditor.vue'
import TaskEditSheet from './TaskEditSheet.vue'
import TaskFilterBar from './TaskFilterBar.vue'
import TaskGroup from './TaskGroup.vue'
import TaskRows from './TaskRows.vue'
import TaskRunHistory from './TaskRunHistory.vue'
import { groupsFor } from '../../utils/taskMeta.js'
import {
  useTaskCounts, useTaskFeed, useTaskFilters, useTaskMutations,
} from '../../composables/useTasks.js'
import { TASK_CAPTURED_EVENT } from '../../composables/useQuickCapture.js'
import { useConfirm } from '../../composables/useConfirm.js'
import { useProjects } from '../../composables/useProjects.js'
import { useTaskExec } from '../../composables/useTaskExec.js'
import { useTaskSelection } from '../../composables/useTaskSelection.js'
import { useTaskShortcuts } from '../../composables/useTaskShortcuts.js'

const route = useRoute()
const filters = useTaskFilters({
  projectId: route.query.project,
  status: route.query.status,
})
const { counts, load: loadCounts } = useTaskCounts(filters.params)
const { patch, bulk, remove, bulkRemove } = useTaskMutations()
const { confirm } = useConfirm()
const { items: projects, ensure: ensureProjects, byId } = useProjects()
const selection = useTaskSelection()

const flat = useTaskFeed(filters.params)
const grouped = computed(() => filters.groupBy.value !== 'none')
// Empty groups are hidden: a backlog of ideas should not render six headers
// reading 0 to say so. The filter bar's total is where "nothing here" is said.
const groups = computed(() =>
  groupsFor(filters.groupBy.value, counts.value, projects.value,
            filters.params.value)
    .filter(g => g.count > 0))
// Counted by the server, so it is true under the current filters even when
// every group is collapsed and no row has been fetched.
const empty = computed(() => counts.value?.total === 0)

const composerOpen = ref(false)
const sheet = ref({ kind: '', task: null })
const filterBar = ref(null)
const groupRefs = ref([])

async function reload() {
  selection.resetVisible()
  await Promise.all([loadCounts(), grouped.value ? null : flat.load()])
  if (!grouped.value) {
    selection.reportVisible(flat.items.value)
    await syncLiveness(flat.items.value)
  }
  groupRefs.value.forEach(g => g?.reload?.())
}

const { refusals, liveness, busyId, control, syncLiveness } = useTaskExec(reload)

async function onGroupLoaded(rows) {
  selection.reportVisible(rows)
  await syncLiveness(rows)
}

// A filter change invalidates the counts AND every group's page, so the whole
// surface reloads rather than showing new headers over old rows.
watch(filters.params, reload, { deep: true })

async function toggleArchive(task) {
  const ok = await patch(task,
    { status: task.status === 'archived' ? 'idea' : 'archived' })
  if (ok) await reload()
}

// Named in the prompt, not counted: "delete 1 task" is a number the operator
// has to trust, while the title is the thing they were looking at.
async function deleteTask(task) {
  const ok = await confirm(
    'Delete task',
    `Delete “${task.title}” and its run history, images and links? `
    + 'This cannot be undone — archive it instead to keep it.', true)
  if (!ok) return
  if (await remove(task)) await reload()
}

async function deleteSelected() {
  const ids = [...selection.ids.value]
  const ok = await confirm(
    'Delete tasks',
    `Delete ${ids.length} ${ids.length === 1 ? 'task' : 'tasks'} and their run `
    + 'history, images and links? This cannot be undone.', true)
  if (!ok) return
  if (!await bulkRemove(ids)) return
  selection.clear()
  await reload()
}

async function applyBulk(fields) {
  const result = await bulk([...selection.ids.value], fields)
  if (!result) return
  selection.clear()
  await reload()
}

function openSheet(kind, task) {
  sheet.value = { kind, task }
}

function closeSheet() {
  sheet.value = { kind: '', task: null }
}

async function sheetDone() {
  closeSheet()
  await reload()
}

// A blocking id links to its task, which the current filter may be hiding — so
// the link clears the filters rather than scrolling to a row that is not
// rendered.
async function focusTask(id) {
  if (filters.active.value) filters.reset()
  await nextTick()
  document.getElementById(`task-${id}`)?.scrollIntoView({ block: 'center' })
}

useTaskShortcuts({
  c: () => { composerOpen.value = true },
  '/': () => filterBar.value?.focusSearch(),
  x: () => selection.toggleAllVisible(),
  Escape: () => selection.clear(),
})

// The bubble captures from any route, this page included; it announces the new
// id rather than reaching into this feed, so the list is refreshed by the
// capture event and never by a timer.
onMounted(() => {
  ensureProjects()
  reload()
  window.addEventListener(TASK_CAPTURED_EVENT, reload)
})
onBeforeUnmount(() => window.removeEventListener(TASK_CAPTURED_EVENT, reload))

defineExpose({ openComposer: () => { composerOpen.value = true } })
</script>

<template>
  <div class="space-y-3">
    <TaskFilterBar
      ref="filterBar"
      :filters="filters"
      :projects="projects"
      :total="counts?.total ?? null"
      @submit-search="filters.submitSearch()"
    />

    <TaskBulkBar
      v-if="selection.count.value"
      :count="selection.count.value"
      :projects="projects"
      @clear="selection.clear()"
      @archive="applyBulk({ status: 'archived' })"
      @delete="deleteSelected"
      @restore="applyBulk({ status: 'idea' })"
      @priority="v => applyBulk({ priority: v })"
      @project="v => applyBulk({ project_id: v === 'none' ? null : Number(v) })"
    />

    <div v-if="selection.visible.value.length" class="tl-selectall">
      <Checkbox
        :model-value="selection.allVisibleSelected.value"
        :indeterminate="selection.someVisibleSelected.value"
        :label="`Select all ${selection.visible.value.length} loaded`"
        data-testid="task-select-all"
        @update:model-value="selection.toggleAllVisible()"
      />
    </div>

    <div v-if="empty" class="empty-state" data-testid="tasks-empty">
      No tasks match this view. Press <kbd>c</kbd> to capture one, or send one
      in with <code>regin task add</code>.
    </div>

    <template v-else-if="grouped">
      <TaskGroup
        v-for="group in groups"
        :key="`${filters.groupBy.value}-${group.key}`"
        ref="groupRefs"
        :group="group"
        :params="filters.params"
        :liveness="liveness"
        :refusals="refusals"
        :selection="selection.ids.value"
        :busy-id="busyId"
        :project-for="t => byId(t.project_id)"
        @loaded="onGroupLoaded"
        @toggle="toggleArchive"
        @open="openSheet"
        @control="control"
        @focus="focusTask"
        @select="selection.toggle"
        @delete="deleteTask"
      />
    </template>

    <template v-else>
      <div v-if="flat.loading.value" class="empty-state">Loading tasks…</div>
      <div v-else-if="flat.error.value" class="empty-state" role="alert">
        {{ flat.error.value }}
      </div>
      <template v-else>
        <div class="tl-list" data-testid="task-list">
          <TaskRows
            :tasks="flat.items.value"
            :liveness="liveness"
            :refusals="refusals"
            :selection="selection.ids.value"
            :busy-id="busyId"
            :project-for="t => byId(t.project_id)"
            @toggle="toggleArchive"
            @open="openSheet"
            @control="control"
            @focus="focusTask"
            @select="selection.toggle"
            @delete="deleteTask"
          />
        </div>
        <div v-if="flat.hasNext.value" class="flex justify-center">
          <Button
            variant="secondary"
            :disabled="flat.loadingMore.value"
            @click="flat.loadMore()"
          >
            {{ flat.loadingMore.value ? 'Loading…' : 'Load more' }}
          </Button>
        </div>
      </template>
    </template>

    <TaskComposerDialog
      v-model:open="composerOpen"
      :projects="projects"
      @created="reload"
    />
    <TaskArmSheet
      v-if="sheet.kind === 'arm'"
      :task="sheet.task"
      @close="closeSheet"
      @armed="sheetDone"
    />
    <TaskDepsEditor
      v-else-if="sheet.kind === 'deps'"
      :task="sheet.task"
      @close="closeSheet"
      @saved="sheetDone"
    />
    <TaskEditSheet
      v-else-if="sheet.kind === 'edit'"
      :task="sheet.task"
      @close="closeSheet"
      @saved="sheetDone"
    />
    <TaskRunHistory
      v-else-if="sheet.kind === 'runs'"
      :task="sheet.task"
      @close="closeSheet"
    />
  </div>
</template>

<style scoped>
.tl-list {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    overflow: hidden;
}
.tl-list :deep(.task-row:last-child) { border-bottom: 0; }
.tl-selectall {
    padding-left: 0.75rem;
    font-size: 0.75rem;
}
</style>
