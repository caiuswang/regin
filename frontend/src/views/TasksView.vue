<script setup>
// The Tasks tab: one toolbar over three readings of one backlog — Board, the
// List (grouped by next step unless another grouping is picked), and the
// Graph of feature lanes. The view is a URL axis (`?view=`), so a reload lands
// where the reader was. Below 768 px the tab is always the grouped list:
// a board or a graph at phone width is a horizontal scroll, not a view.
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import DropdownMenu from '../components/ui/DropdownMenu.vue'
import NextStepList from '../components/tasks/NextStepList.vue'
import TaskBoard from '../components/tasks/TaskBoard.vue'
import TaskFilterMenu from '../components/tasks/TaskFilterMenu.vue'
import TaskLaneGraph from '../components/tasks/TaskLaneGraph.vue'
import TaskListPanel from '../components/tasks/TaskListPanel.vue'
import TaskToolbar from '../components/tasks/TaskToolbar.vue'
import TasksScopeBanner from '../components/tasks/TasksScopeBanner.vue'
import PipelineCreateButton from '../components/pipeline/PipelineCreateButton.vue'
import PlSlot from '../components/pipeline/PlSlot.vue'
import { usePipelineCreate } from '../composables/usePipelineCreate'
import { usePipelineTabActive } from '../composables/usePipelineHeader'
import { invalidatePipelineCounts } from '../composables/usePipelineCounts.js'
import { useBreakpoint } from '../composables/useBreakpoint.js'
import { provideJustCreated } from '../composables/useJustCreated.js'
import { TASK_CAPTURED_EVENT } from '../composables/useQuickCapture.js'
import { useTaskQuery } from '../composables/useTaskQuery'
import { useTaskShortcuts } from '../composables/useTaskShortcuts.js'
import { useUsers } from '../composables/useUsers.js'
import { usePipelineLive } from '../composables/usePipelineEvents'
import { taskChips } from '../utils/taskChips.js'
import { GROUPS, SORT_KEYS } from '../utils/taskMeta.js'
import api from '../api'

const route = useRoute()
const router = useRouter()
const query = useTaskQuery()
const isCurrentTab = usePipelineTabActive()

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const scopeHead = ref(null)
let featureAsked = 0
async function loadScopeHead() {
  const id = query.state.value.feature_id
  const mine = ++featureAsked
  // Another feature's head must not stand in while this one is asked for: its
  // primary button would act on that feature and its project leak into a create.
  if (scopeHead.value?.id !== Number(id)) scopeHead.value = null
  if (!id) return
  try {
    const head = await api.get(`/features/${id}/next-step`)
    if (mine === featureAsked) scopeHead.value = head
  } catch {
    if (mine === featureAsked) scopeHead.value = null
  }
}
watch(() => query.state.value.feature_id, loadScopeHead, { immediate: true })
const featureRow = computed(() => (scopeHead.value
  ? { id: scopeHead.value.id, title: scopeHead.value.title } : null))
// Under a feature scope every create files the task there — still a chip the
// operator can clear in the dialog. The id alone is enough before the head
// answers; the project rides along only once it is known.
const createPrefill = computed(() => {
  const id = query.state.value.feature_id
  if (!id) return null
  const head = scopeHead.value
  const feature = { id: Number(id), title: head?.title ?? `#${id}` }
  if (head && 'project_id' in head) feature.project_id = head.project_id
  return { feature }
})
function createTask() {
  openCreate('task', createPrefill.value)
}
const { isMdUp } = useBreakpoint()
const { items: users, ensure: ensureUsers } = useUsers()

const mobile = computed(() => !isMdUp.value)
const view = computed(() => (mobile.value ? 'list' : query.state.value.view))
const nextStep = computed(() => view.value === 'list' && query.state.value.group === 'next_step')
const mode = computed(() => {
  if (view.value === 'graph') return 'graph'
  return view.value === 'board' || nextStep.value ? 'grouped' : 'flat'
})

// A bare /tasks is restored from the stored filters before any body reads,
// so the first request is issued once, under the query actually on screen.
// Kept alive: while another tab is on screen its query is not this tab's, so
// the bodies keep the params they last had rather than reloading under it.
const params = ref(query.restoreStored() ? null : query.params.value)
watch(() => query.params.value, (next) => {
  if (route.path === '/tasks') params.value = next
})

const sortKey = computed(() => query.state.value.sort.split(':')[0])
const sortDir = computed(() => (query.state.value.sort.split(':')[1] === 'asc' ? 'asc' : 'desc'))

const chips = computed(() => taskChips(query.state.value,
  { mode: mode.value, mobile: mobile.value, users: users.value, feature: featureRow.value }))
const count = ref('')
const find = ref('')
const body = ref(null)
const toolbar = ref(null)
const root = ref(null)
const { openCreate } = usePipelineCreate()

const groupOptions = GROUPS.map(g => ({
  value: g.value, label: g.value === 'none' ? g.label : `Group: ${g.label}`, short: g.label }))
// Phones get the same two axes as the desktop toolbar, as menus: what the list
// is grouped by, and — where the rows are ordered by it — what it is sorted by.
const groupItems = computed(() => GROUPS.map(g => ({
  label: g.value === query.state.value.group ? `${g.label} (current)` : g.label,
  testid: `task-group-${g.value}`,
  onSelect: () => query.patch({ group: g.value }) })))
const sortItems = computed(() => [
  ...SORT_KEYS.map(k => ({
    label: k.value === sortKey.value ? `${k.label} (current)` : k.label,
    testid: `task-sort-${k.value}`,
    onSelect: () => query.patch({ sort: `${k.value}:${sortDir.value}` }) })),
  { label: sortDir.value === 'asc' ? 'Descending' : 'Ascending',
    testid: 'task-sort-dir',
    onSelect: () => query.patch({ sort: `${sortKey.value}:${sortDir.value === 'asc' ? 'desc' : 'asc'}` }) },
])

function setView(v) {
  count.value = ''
  query.patch({ view: v })
}

function search(term) {
  if (view.value === 'graph') find.value = term
  else if (term !== query.state.value.q) query.patch({ q: term })
}

async function afterWrite() {
  invalidatePipelineCounts()
  await Promise.all([body.value?.reload?.(), loadScopeHead()])
}

// Show widens to every task, the address included — a row the scope itself
// hides is still the row the reader just made — and to the flat list newest
// first, the one reading where a new row is on the first page and in no
// collapsed group. A route, not `query.patch`: a way to one row is not a
// grouping or a filter the reader chose to keep, so nothing is stored.
const justCreated = provideJustCreated({
  root,
  noun: 'Task',
  reveal: () => router.replace({
    path: route.path,
    query: { ...(route.query.project ? { project: route.query.project } : {}), group: 'none' },
  }),
  reload: () => body.value?.reload?.(),
})

// Kept alive, this tab hears a capture made on any other: only the tab on
// screen has rows to point at. The graph marks no rows — its finder is the
// way to a node.
async function captured(event) {
  const id = event?.detail?.id
  if (!isCurrentTab.value || view.value === 'graph' || id == null) return afterWrite()
  invalidatePipelineCounts()
  await justCreated.land(id, () => Promise.all([body.value?.reload?.(), loadScopeHead()]))
}

usePipelineLive(() => Promise.all([body.value?.reload?.(), loadScopeHead()]))

useTaskShortcuts({
  c: createTask,
  '/': () => toolbar.value?.focusSearch(),
})

onMounted(() => {
  ensureUsers()
  window.addEventListener(TASK_CAPTURED_EVENT, captured)
})
// Kept alive, so a second arrival at a bare /tasks (the sidebar, a deep link)
// restores the same stored filters a cold load would.
onActivated(() => { query.restoreStored() })
onBeforeUnmount(() => window.removeEventListener(TASK_CAPTURED_EVENT, captured))
</script>

<template>
  <div ref="root" class="tv space-y-3" data-pl-part="panel">
    <Teleport v-if="isCurrentTab" defer to="#pipeline-actions">
      <PipelineCreateButton kind="task" testid="task-new" :prefill="createPrefill" />
    </Teleport>

    <TaskToolbar
      ref="toolbar"
      data-pl-part="panel-head"
      data-pl-title="Quest log"
      :view="view"
      :mobile="mobile"
      :q="view === 'graph' ? find : query.state.value.q"
      :placeholder="view === 'graph' ? 'Find a task in the graph' : 'Search tasks'"
      :chips="chips"
      @patch="query.patch"
      @view="setView"
      @search="search"
    >
      <template #filter>
        <TaskFilterMenu
          :state="query.state.value"
          :mode="mode"
          :users="users"
          :mobile="mobile"
          :feature="featureRow"
          @patch="query.patch"
        />
        <DropdownMenu v-if="mobile" :items="groupItems" align="start">
          <template #trigger>
            <Button variant="ghost" class="tv-sort" data-pl-part="key" data-testid="task-group-menu">Group</Button>
          </template>
        </DropdownMenu>
        <DropdownMenu v-if="mobile && mode === 'flat'" :items="sortItems" align="start">
          <template #trigger>
            <Button variant="ghost" class="tv-sort" data-pl-part="key" data-testid="task-sort">Sort</Button>
          </template>
        </DropdownMenu>
      </template>
      <template #meta>
        <span v-if="mobile" class="tv-count" data-pl-part="panel-meta" data-testid="list-count">{{ count }}</span>
        <template v-else-if="view === 'graph'">
          <span class="tv-legend" data-pl-part="meta-label"><span class="tv-swatch is-blocking" />blocking</span>
          <span class="tv-legend" data-pl-part="meta-label"><span class="tv-swatch" />depends on</span>
        </template>
        <template v-else>
          <span v-if="count" class="tv-count" data-pl-part="panel-meta" data-testid="list-count">{{ count }}</span>
          <span v-if="mode === 'flat'" class="tv-sort-group">
            <PlSlot
              name="MenuField"
              :model-value="sortKey"
              :options="SORT_KEYS"
              field="SORT"
              head="Sort tasks by"
              aria-label="Sort by"
              testid="list-sort"
              @update:model-value="v => query.patch({ sort: `${v}:${sortDir}` })"
            />
            <Button
              variant="secondary"
              size="sm"
              :aria-label="sortDir === 'asc' ? 'Sorted ascending — switch to descending' : 'Sorted descending — switch to ascending'"
              data-testid="list-sort-dir"
              @click="query.patch({ sort: `${sortKey}:${sortDir === 'asc' ? 'desc' : 'asc'}` })"
            >
              <Icon :name="sortDir === 'asc' ? 'arrow-up' : 'arrow-down'" :size="12" />
            </Button>
          </span>
          <PlSlot
            v-if="view === 'list'"
            name="MenuField"
            :model-value="query.state.value.group"
            :options="groupOptions"
            field="GROUP"
            head="Group tasks by"
            aria-label="Group by"
            testid="task-group"
            class="tv-group"
            @update:model-value="v => query.patch({ group: v })"
          />
          <span v-else class="tv-count" data-pl-part="panel-meta" title="Columns sort by priority; Running by latest start; Done newest first">
            Priority <Icon name="arrow-down" :size="12" />
          </span>
        </template>
      </template>
    </TaskToolbar>

    <TasksScopeBanner
      :feature-id="query.state.value.feature_id"
      :head="scopeHead"
      :can-write="canWrite"
      @new-task="createTask"
      @changed="afterWrite"
    />

    <TaskBoard
      v-if="view === 'board' && params"
      ref="body"
      :params="params"
      @count="v => (count = v)"
      @compose="createTask"
      @view="setView"
    />
    <TaskLaneGraph
      v-else-if="view === 'graph' && params"
      ref="body"
      :params="params"
      :open="query.state.value.open !== '0'"
      :find="find"
    />
    <template v-else-if="nextStep">
      <NextStepList v-if="params" ref="body" :params="params" :anchor="query.anchor.value" @count="v => (count = v)" />
    </template>
    <TaskListPanel v-else ref="body" @count="v => (count = v)" />

  </div>
</template>

<style scoped>
.tv-count {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}
.tv-legend {
    display: inline-flex;
    align-items: center;
    gap: 0.4375rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.tv-swatch {
    width: 1.25rem;
    border-top: 1.5px solid var(--color-border-strong);
}
.tv-swatch.is-blocking {
    border-top: 2px dashed var(--color-warning);
}
.tv-sort-group {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
}
.tv .tv-sort {
    height: 2.75rem;
    border: 1px dashed var(--color-border-strong);
}
</style>
