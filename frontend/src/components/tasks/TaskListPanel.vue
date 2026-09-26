<script setup>
// The /tasks list surface: filter, group, select, act.
//
// Every refresh here is caused by something — a mount, a filter change, a
// mutation that resolved, a capture announced on the window — never by a
// timer. And every filter is a query parameter in `location.search`, so what
// the counts say, what the list pages through, and what a link someone pastes
// into chat will show are the same set.
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Icon from '../ui/Icon.vue'
import Select from '../ui/Select.vue'
import ListStatus from '../ListStatus.vue'
import PageControls from '../PageControls.vue'
import PipelineListToolbar from '../PipelineListToolbar.vue'
import TaskArmSheet from './TaskArmSheet.vue'
import TaskBulkBar from './TaskBulkBar.vue'
import TaskComposerDialog from './TaskComposerDialog.vue'
import TaskDepsEditor from './TaskDepsEditor.vue'
import TaskEditSheet from './TaskEditSheet.vue'
import TaskMoreFilters from './TaskMoreFilters.vue'
import TaskGroup from './TaskGroup.vue'
import TaskRows from './TaskRows.vue'
import TaskRunHistory from './TaskRunHistory.vue'
import TasksScopeBanner from './TasksScopeBanner.vue'
import { GROUPS, PRIORITIES, SORT_KEYS, STATUS_FILTERS, groupsFor } from '../../utils/taskMeta.js'
import { useTaskCounts, useTaskMutations } from '../../composables/useTasks.js'
import { TASK_CAPTURED_EVENT } from '../../composables/useQuickCapture.js'
import { useConfirm } from '../../composables/useConfirm.js'
import { listStatusState } from '../../composables/useListQuery.js'
import { usePage } from '../../composables/usePage.js'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'
import { useGroupCollapse } from '../../composables/useGroupCollapse.js'
import { useProjects } from '../../composables/useProjects.js'
import { useTaskExec } from '../../composables/useTaskExec.js'
import { useTaskQuery } from '../../composables/useTaskQuery.js'
import { useTaskSelection } from '../../composables/useTaskSelection.js'
import { useTaskShortcuts } from '../../composables/useTaskShortcuts.js'
import { useUsers } from '../../composables/useUsers.js'

const FLAT = 'flat'

const route = useRoute()
const query = useTaskQuery()
const { counts, error: countsError, load: loadCounts } = useTaskCounts(query.params)
const { patch, bulk, remove, bulkRemove } = useTaskMutations()
const { confirm } = useConfirm()
const { items: projects, ensure: ensureProjects, byId } = useProjects()
const { items: users, ensure: ensureUsers } = useUsers()
const { isOpen, setOpen } = useGroupCollapse()
const selection = useTaskSelection()

const views = [{ value: 'list', label: 'List' }, { value: 'graph', label: 'Graph' }]
const emit = defineEmits(['view'])

// The flat list pages by offset, like every other Pipeline list; the grouped
// list is built out of the counts, and each group appends pages of its own.
const flat = usePage({ path: '/tasks', buildQuery: () => query.params.value })
const groupMode = computed(() => query.state.value.group !== 'none')
// Counts the server could not give cannot produce headers, so the list then
// falls back to the flat feed rather than rendering an empty page.
const grouped = computed(() => groupMode.value && counts.value != null)
// Empty groups are hidden: a backlog of ideas should not render six headers
// reading 0 to say so.
const groups = computed(() =>
  groupsFor(query.state.value.group, counts.value, projects.value,
            query.params.value)
    .filter(g => g.count > 0))

const shownTotal = computed(() => (grouped.value ? counts.value?.total ?? null
  : flat.loaded.value && !flat.error.value ? flat.total.value : null))
const population = computed(() => (grouped.value ? counts.value?.population ?? null
  : flat.population.value))
const appliedSort = computed(() => (!grouped.value && flat.applied.value?.sort)
  || query.state.value.sort)
const sortKey = computed(() => appliedSort.value.split(':')[0])
const sortDir = computed(() => (appliedSort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))
const groupsLoaded = ref(false)
const status = computed(() => (grouped.value
  ? listStatusState({ loaded: groupsLoaded.value, error: '', items: groups.value,
                      total: counts.value?.total, population: counts.value?.population })
  : listStatusState({ loaded: flat.loaded.value, error: flat.error.value,
                      items: flat.items.value, total: flat.total.value,
                      population: flat.population.value })))

const composerOpen = ref(false)
const sheet = ref({ kind: '', task: null })
const toolbar = ref(null)
const groupRefs = ref([])

async function reload() {
  lastKey = paramsKey.value
  selection.resetVisible()
  if (groupMode.value) {
    await loadCounts()
    // Headers from a previous predicate would describe other rows: a failed
    // read drops them, and the list falls back to the flat feed and says why.
    if (countsError.value) counts.value = null
    groupsLoaded.value = true
  }
  if (!grouped.value) {
    flat.page.value = query.page.value
    flat.pageSize.value = query.size.value
    await flat.load()
    // Only when ungrouped: in group mode a failed counts read is what put the
    // list here, and adopting these would flip it back into groups.
    if (!groupMode.value && flat.extras.value?.counts) counts.value = flat.extras.value.counts
    selection.reportVisible(FLAT, flat.items.value)
    await syncLiveness(flat.items.value)
  }
  groupRefs.value.forEach(g => g?.reload?.())
}

const { refusals, liveness, busyId, control, syncLiveness } = useTaskExec(reload)

// A write changes a count the shell shows, and the shell cannot see it.
async function afterWrite() {
  invalidatePipelineCounts()
  await reload()
}

function gotoPage(index) {
  query.patch({ page: String(Math.max(0, Math.min(flat.pageCount.value - 1, index))) })
}

async function onGroupLoaded(key, rows) {
  selection.reportVisible(key, rows)
  await syncLiveness(rows)
}

// A filter change invalidates the counts AND every group's page, so the whole
// surface reloads rather than showing new headers over old rows.
//
// Keyed by the query itself rather than by the computed's identity: `params`
// is rebuilt on every route change, so a rewrite that leaves the server query
// alone — restoring `group`, which is rendered client-side — would otherwise
// read as a filter change and cost the page a second round of requests.
// Kept alive: while another tab is on screen its query is not this list's,
// so the key goes quiet (null) rather than reloading under a foreign query.
const paramsKey = computed(() => (route.path !== '/tasks' ? null : JSON.stringify(
  [query.params.value, query.page.value, query.size.value, groupMode.value])))
let lastKey = null
watch(paramsKey, (key) => { if (key != null && key !== lastKey) reload() })

async function toggleArchive(task) {
  const ok = await patch(task,
    { status: task.status === 'archived' ? 'idea' : 'archived' })
  if (ok) await afterWrite()
}

// Named in the prompt, not counted: "delete 1 task" is a number the operator
// has to trust, while the title is the thing they were looking at.
async function deleteTask(task) {
  const ok = await confirm(
    'Delete task',
    `Delete “${task.title}” and its run history, images and links? `
    + 'This cannot be undone — archive it instead to keep it.', true)
  if (!ok) return
  if (await remove(task)) await afterWrite()
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
  await afterWrite()
}

async function applyBulk(fields) {
  const result = await bulk([...selection.ids.value], fields)
  if (!result) return
  selection.clear()
  await afterWrite()
}

function openSheet(kind, task) {
  sheet.value = { kind, task }
}

function closeSheet() {
  sheet.value = { kind: '', task: null }
}

async function sheetDone() {
  closeSheet()
  await afterWrite()
}

// A blocking id links to its task, which the current filter may be hiding — so
// the link clears the filters rather than scrolling to a row that is not
// rendered.
async function focusTask(id) {
  // The scope is deliberately left alone: it is the shell's, shared with the
  // other two tabs, and widening it from here would move a page nobody is
  // looking at.
  const s = query.state.value
  if (query.active.value || s.feature_id || s.assignee) query.reset({ arrival: true })
  await nextTick()
  document.getElementById(`task-${id}`)?.scrollIntoView({ block: 'center' })
}

useTaskShortcuts({
  c: () => { composerOpen.value = true },
  '/': () => toolbar.value?.focusSearch(),
  x: () => selection.toggleAllVisible(),
  Escape: () => selection.clear(),
})

// The bubble captures from any route, this page included; it announces the new
// id rather than reaching into this feed, so the list is refreshed by the
// capture event and never by a timer.
onMounted(() => {
  ensureProjects()
  ensureUsers()
  // A restore rewrites the URL, and the params watch above is what loads the
  // result — so the first request is issued once, under the query that is
  // actually on screen, rather than once bare and again restored.
  if (!query.restoreStored()) reload()
  window.addEventListener(TASK_CAPTURED_EVENT, afterWrite)
})

// The tab is kept alive, so `onMounted` runs once for the life of the page and
// cannot answer for the second arrival. Coming back to a bare `/tasks` — from
// the sidebar, a deep link, any route that is not the tab strip's own link —
// has to restore the same stored filters a cold load would, or the same URL
// means two different things. No reload here: the params watch answers if the
// restore moved the query, and a re-entry that did not is already loaded.
onActivated(() => { query.restoreStored() })

onBeforeUnmount(() => window.removeEventListener(TASK_CAPTURED_EVENT, afterWrite))

const statusOptions = STATUS_FILTERS.map(
  s => ({ value: s.key, label: s.key ? s.label : 'Any status' }))
const priorityOptions = [
  { value: '', label: 'Any priority' },
  ...PRIORITIES.map(p => ({ value: p.value, label: p.label })),
]
// "Status" beside "Any status" reads as a second status filter.
const groupOptions = GROUPS.map(g => ({
  value: g.value, label: g.value === 'none' ? g.label : `Group: ${g.label}` }))
const archived = computed(() => query.state.value.archived === '1')
const runs = computed(() => query.state.value.runs === '1')

defineExpose({ openComposer: () => { composerOpen.value = true } })
</script>

<template>
  <div class="space-y-3">
    <PipelineListToolbar
      ref="toolbar"
      noun="task"
      :views="views"
      view="list"
      :q="query.state.value.q"
      search-label="Search tasks"
      search-placeholder="Search titles and details…"
      :facets="[
        { key: 'status', label: 'Filter by status', value: query.state.value.status, options: statusOptions },
        { key: 'priority', label: 'Filter by priority', value: query.state.value.priority, options: priorityOptions },
      ]"
      :sort-options="SORT_KEYS"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      :total="shownTotal"
      :population="population"
      :dirty-count="query.active.value"
      @view="v => emit('view', v)"
      @patch="query.patch"
      @reset="query.reset()"
    >
      <template #controls>
        <Select
          :model-value="query.state.value.group"
          :options="groupOptions"
          aria-label="Group by"
          data-testid="task-group"
          @update:model-value="v => query.patch({ group: v })"
        />
        <TaskMoreFilters :state="query.state.value" :users="users" @patch="query.patch" />
        <Button
          variant="ghost"
          size="sm"
          :aria-pressed="archived"
          :aria-label="archived ? 'Hide archived tasks' : 'Show archived tasks'"
          data-testid="task-archived-toggle"
          @click="query.patch({ archived: archived ? '' : '1' })"
        >
          <Icon name="archive" :size="13" />
          <span>{{ archived ? 'Hide archived' : 'Show archived' }}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          :aria-pressed="runs"
          :aria-label="runs ? 'Hide feature steps' : 'List feature steps beside top-level tasks'"
          data-testid="task-runs-toggle"
          @click="query.patch({ runs: runs ? '' : '1' })"
        >
          <Icon name="list" :size="13" />
          <span>{{ runs ? 'Hide feature steps' : 'Show feature steps' }}</span>
        </Button>
      </template>
    </PipelineListToolbar>

    <TasksScopeBanner :feature-id="query.state.value.feature_id" />

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

    <p v-if="countsError" class="tl-counts-error" role="status" data-testid="task-counts-error">
      Task counts unavailable — {{ countsError }}
    </p>

    <ListStatus
      :state="status"
      noun="task"
      :total="shownTotal ?? 0"
      :page="flat.page.value"
      :dirty-count="query.active.value"
      :error="flat.error.value || ''"
      empty-text="No tasks yet. Press c to capture one, or send one in with regin task add."
      @retry="reload"
      @clear="query.reset()"
      @first-page="query.patch({ page: '0' })"
    >
      <template #create>
        <Button variant="secondary" size="sm" @click="composerOpen = true">New task</Button>
      </template>
    </ListStatus>

    <template v-if="status === 'rows' && grouped">
      <TaskGroup
        v-for="group in groups"
        :key="`${query.state.value.group}-${group.key}`"
        ref="groupRefs"
        :open="isOpen(query.state.value.group, group.key)"
        :group="group"
        :params="query.params"
        :liveness="liveness"
        :refusals="refusals"
        :selection="selection.ids.value"
        :busy-id="busyId"
        :project-for="t => byId(t.project_id)"
        @update:open="v => setOpen(query.state.value.group, group.key, v)"
        @loaded="onGroupLoaded"
        @hidden="selection.withdrawVisible"
        @toggle="toggleArchive"
        @open="openSheet"
        @control="control"
        @focus="focusTask"
        @select="selection.toggle"
        @delete="deleteTask"
      />
    </template>

    <template v-else-if="status === 'rows'">
      <div class="tl-list" :class="{ 'is-busy': flat.loading.value }" :aria-busy="flat.loading.value" data-testid="task-list">
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
    </template>

    <PageControls
      v-if="!grouped && flat.loaded.value && flat.total.value > flat.pageSize.value"
      :page="flat.page.value"
      :page-count="flat.pageCount.value"
      :total="flat.total.value"
      :size="flat.pageSize.value"
      :has-next="flat.hasNext.value"
      :has-prev="flat.hasPrev.value"
      :loading="flat.loading.value"
      @prev="gotoPage(flat.page.value - 1)"
      @next="gotoPage(flat.page.value + 1)"
      @goto="gotoPage"
      @set-size="n => query.patch({ size: String(n) })"
    />

    <TaskComposerDialog
      v-model:open="composerOpen"
      :projects="projects"
      :project="query.project.value"
      @created="afterWrite"
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
.tl-counts-error {
    font-size: 0.8125rem;
    color: var(--color-text-muted, #6b7280);
}

.tl-list.is-busy { opacity: 0.6; }
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
