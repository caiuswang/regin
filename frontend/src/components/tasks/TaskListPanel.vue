<script setup>
// The /tasks list surface: filter, group, select, act.
//
// Every refresh here is caused by something — a mount, a filter change, a
// mutation that resolved, a capture announced on the window — never by a
// timer. And every filter is a query parameter in `location.search`, so what
// the counts say, what the list pages through, and what a link someone pastes
// into chat will show are the same set.
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import BulkRejected from './BulkRejected.vue'
import Checkbox from '../ui/Checkbox.vue'
import Icon from '../ui/Icon.vue'
import ListStatus from '../ListStatus.vue'
import PageControls from '../PageControls.vue'
import TaskArmSheet from './TaskArmSheet.vue'
import TaskBulkBar from './TaskBulkBar.vue'
import TaskDepsEditor from './TaskDepsEditor.vue'
import TaskEditSheet from './TaskEditSheet.vue'
import TaskGroup from './TaskGroup.vue'
import TaskRows from './TaskRows.vue'
import TaskRunHistory from './TaskRunHistory.vue'
import { groupsFor } from '../../utils/taskMeta.js'
import { useTaskCounts, useTaskMutations } from '../../composables/useTasks.js'
import { TASK_CAPTURED_EVENT } from '../../composables/useQuickCapture.js'
import { useConfirm } from '../../composables/useConfirm.js'
import { listStatusState } from '../../composables/useListQuery.js'
import { usePage } from '../../composables/usePage.js'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'
import { useGroupCollapse } from '../../composables/useGroupCollapse.js'
import { useProjects } from '../../composables/useProjects.js'
import { useTaskQuery } from '../../composables/useTaskQuery.js'
import { useTaskSelection } from '../../composables/useTaskSelection.js'
import { useTaskShortcuts } from '../../composables/useTaskShortcuts.js'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'

const FLAT = 'flat'

const route = useRoute()
const router = useRouter()
const query = useTaskQuery()
const canWrite = api.getStoredUser()?.role !== 'viewer'
const { counts, error: countsError, load: loadCounts } = useTaskCounts(query.params)
const { bulk, bulkRemove } = useTaskMutations()
const { confirm } = useConfirm()
const { items: projects, ensure: ensureProjects, byId } = useProjects()
const { isOpen, setOpen } = useGroupCollapse()
const selection = useTaskSelection()

const emit = defineEmits(['count'])

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

// R4: unnarrowed → "M tasks"; narrowed → "N of M tasks". A widening control
// (archived, feature steps) can make N exceed M, and "12 of 9" is nonsense,
// so that reading states N alone.
const countText = computed(() => {
  const total = shownTotal.value
  const pop = population.value
  if (total == null || pop == null) return ''
  const noun = n => (n === 1 ? 'task' : 'tasks')
  const narrowed = query.active.value > 0 || total !== pop
  if (narrowed && total <= pop) return `${total} of ${pop} ${noun(pop)}`
  return `${total} ${noun(total)}`
})
watch(countText, text => emit('count', text), { immediate: true })
const groupsLoaded = ref(false)
const status = computed(() => (grouped.value
  ? listStatusState({ loaded: groupsLoaded.value, error: '', items: groups.value,
                      total: counts.value?.total, population: counts.value?.population })
  : listStatusState({ loaded: flat.loaded.value, error: flat.error.value,
                      items: flat.items.value, total: flat.total.value,
                      population: flat.population.value })))

const sheet = ref({ kind: '', task: null })
const groupRefs = ref([])
const bulkRejected = ref([])
// Every row this surface has rendered, by id, so a refusal can name what it
// refused even after the reload that follows the write has moved it away.
const titles = new Map()

const dateField = computed(() => (query.state.value.sort.startsWith('updated')
  ? 'updated_at' : 'created_at'))

function remember(rows) {
  for (const row of rows) titles.set(row.id, row.title)
}

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
    remember(flat.items.value)
    await syncLiveness(flat.items.value)
  }
  groupRefs.value.forEach(g => g?.reload?.())
}

const { refusals, liveness, busyId, syncLiveness, act } = useTaskVerbs(afterWrite, { openSheet })

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
  remember(rows)
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

async function deleteSelected() {
  const ids = [...selection.ids.value]
  const ok = await confirm(
    'Delete tasks',
    `Delete ${ids.length} ${ids.length === 1 ? 'task' : 'tasks'} and their run `
    + 'history, images and links? This cannot be undone.', true)
  if (!ok) return
  const result = await bulkRemove(ids)
  if (!result) return
  nameRejected(result)
  selection.clear()
  await afterWrite()
}

function nameRejected(result) {
  bulkRejected.value = (result.rejected || []).map(r => ({ ...r, title: titles.get(r.id) || '' }))
}

async function applyBulk(fields) {
  const result = await bulk([...selection.ids.value], fields)
  if (!result) return
  nameRejected(result)
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

// A blocking id opens its task, as it does on the detail page. Scrolling to it
// in place could not work: the current filter or page may not hold it.
function focusTask(id) {
  router.push(`/tasks/${id}`)
}

useTaskShortcuts({
  x: () => { if (canWrite) selection.toggleAllVisible() },
  Escape: () => selection.clear(),
})

// The bubble captures from any route, this page included; it announces the new
// id rather than reaching into this feed, so the list is refreshed by the
// capture event and never by a timer.
onMounted(() => {
  ensureProjects()
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

defineExpose({ reload: afterWrite })
</script>

<template>
  <div class="space-y-3">

    <TaskBulkBar
      v-if="canWrite && selection.count.value"
      :count="selection.count.value"
      :projects="projects"
      @clear="selection.clear()"
      @archive="applyBulk({ status: 'archived' })"
      @delete="deleteSelected"
      @restore="applyBulk({ status: 'idea' })"
      @priority="v => applyBulk({ priority: v })"
      @project="v => applyBulk({ project_id: v === 'none' ? null : Number(v) })"
      @feature="id => applyBulk({ feature_id: id })"
    />

    <BulkRejected v-if="bulkRejected.length" :rows="bulkRejected" @dismiss="bulkRejected = []" />

    <div v-if="canWrite && selection.visible.value.length" class="tl-selectall">
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
        :can-write="canWrite"
        :date-field="dateField"
        @update:open="v => setOpen(query.state.value.group, group.key, v)"
        @loaded="onGroupLoaded"
        @hidden="selection.withdrawVisible"
        @verb="act"
        @open="openSheet"
        @focus="focusTask"
        @select="selection.toggle"
      />
    </template>

    <template v-else-if="status === 'rows'">
      <div class="tl-list" :class="{ 'is-busy': flat.loading.value }" :aria-busy="flat.loading.value" data-testid="task-list">
        <div class="tl-head" :class="{ 'can-write': canWrite }" aria-hidden="true">
          <span class="tl-head-day" data-testid="task-day-head">{{ dateField === 'updated_at' ? 'Updated' : 'Created' }}</span>
        </div>
        <TaskRows
          :tasks="flat.items.value"
          :liveness="liveness"
          :refusals="refusals"
          :selection="selection.ids.value"
          :busy-id="busyId"
          :project-for="t => byId(t.project_id)"
          :can-write="canWrite"
          :date-field="dateField"
          @verb="act"
          @open="openSheet"
          @focus="focusTask"
          @select="selection.toggle"
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
/* One label over the rows' date column. The right padding is the row's
   padding, its action cluster's primary slot (8.5rem) and the row gap before
   it — plus, for an editor, the ⋯ trigger and its gap. */
.tl-head {
    display: flex;
    justify-content: flex-end;
    padding: 0.3125rem calc(0.75rem + 8.5rem + 0.5rem) 0.3125rem 0.75rem;
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--color-fg-subtle);
    background: var(--color-surface-2);
    border-bottom: 1px solid var(--color-border-subtle);
}
.tl-head.can-write { padding-right: calc(0.75rem + 8.5rem + 0.5rem + 0.25rem + var(--tl-menu-w, 2.125rem)); }
.tl-head-day { flex: 0 0 5.25rem; text-align: right; }
@media (max-width: 40rem) {
    .tl-head { display: none; }
}
.tl-selectall {
    padding-left: 0.75rem;
    font-size: 0.75rem;
}
</style>
