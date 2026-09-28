<script setup>
// The pipeline's one workbench: Overview, Tasks and Features behind a one-line
// header. Defects and projects are not tabs — defects are reached from the
// Overview's "Needs a decision", the Features triage rail and expanded feature
// rows; projects from the chip's picker and the Overview's Projects panel.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ProjectPicker from '../components/pipeline/ProjectPicker.vue'
import { usePipelineHeader } from '../composables/usePipelineHeader'
import { usePipelineCounts, usePipelineUpdatedAt } from '../composables/usePipelineCounts'
import { usePipelineTabState } from '../composables/usePipelineTabState'
import { usePipelineScope, persistScope, publishShellScope, scopeQuery, storedScope,
  SCOPE_ALL, SCOPE_NONE }
  from '../composables/usePipelineScope'
import { useProjects } from '../composables/useProjects'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import { useBreakpoint } from '../composables/useBreakpoint'
import { usePipelineCreate } from '../composables/usePipelineCreate'
import { invalidatePipelineCounts } from '../composables/usePipelineCounts'
import { usePipelineLive } from '../composables/usePipelineEvents'
import { TASK_CAPTURED_EVENT } from '../composables/useQuickCapture.js'
import { useFlash } from '../composables/useFlash'
import api from '../api'
import TaskComposerDialog from '../components/tasks/TaskComposerDialog.vue'
import NewFeatureDialog from '../components/features/NewFeatureDialog.vue'
import NewDefectDialog from '../components/defects/NewDefectDialog.vue'

const route = useRoute()
const router = useRouter()

const TABS = [
  { path: '/overview', key: 'overview', label: 'Overview', counted: false },
  { path: '/tasks', key: 'tasks', label: 'Tasks', counted: true },
  // Defects are reached from the triage rail, so their pages light Features.
  { path: '/features', key: 'features', label: 'Features', counted: true, also: ['/defects'] },
]

function isActive(tab) {
  const p = route.path
  return p === tab.path || p.startsWith(`${tab.path}/`)
    || (tab.also || []).some(prefix => p.startsWith(prefix))
}

const onTab = computed(() => TABS.some(t => t.path === route.path))
// A detail route is a page of its own, not a tab: it keeps the strip above it
// but must never be cached, or two ids would share one mount.
const CACHED_TABS = ['OverviewView', 'TasksView', 'FeaturesView']

const header = usePipelineHeader(() => route.path)
const { scope, setScope, restoreStored } = usePipelineScope()
const { counts, error: countsError, load: loadCounts, revision: countsRevision }
  = usePipelineCounts()
const updatedAt = usePipelineUpdatedAt()
const { queryFor } = usePipelineTabState(p => TABS.some(t => t.path === p))
const { items: projects, ensure: ensureProjects } = useProjects()
const { isMdUp } = useBreakpoint()

const isLegalScope = v => v === SCOPE_NONE || /^\d+$/.test(v)
// A detail page has no `?project=` of its own, so the strip beside it carries
// the scope a tab was last left under in this visit (a URL-only scope is never
// persisted), else the stored one.
const lastScoped = ref(null)
watch([onTab, scope], ([isTab, value]) => { if (isTab) lastScoped.value = value },
  { immediate: true })
const carried = computed(() => {
  if (onTab.value) return scope.value
  const candidate = lastScoped.value || storedScope()
  return isLegalScope(candidate) ? candidate : SCOPE_ALL
})

const scopeLabel = computed(() => {
  const s = carried.value
  if (s === SCOPE_ALL) return 'All projects'
  if (s === SCOPE_NONE) return 'No project'
  return projects.value.find(p => String(p.id) === s)?.name || `project #${s}`
})

// A detail page is one row, and a row does not change project with the
// scope — so a new scope on a detail leaves for the list it belongs to.
function parentList(path) {
  if (path.startsWith('/defects')) return '/defects'
  const tab = TABS.find(t => path.startsWith(`${t.path}/`))
  return tab ? tab.path : '/overview'
}

function pickScope(next) {
  if (onTab.value || route.path === '/defects') {
    setScope(next)
    return
  }
  persistScope(next)
  const path = parentList(route.path)
  router.push({ path, query: queryFor(path, scopeQuery(next)) })
}

function tabTo(tab) {
  return { path: tab.path, query: queryFor(tab.path, scopeQuery(carried.value)) }
}

function tabCount(tab) {
  const n = counts.value?.[tab.key]
  return tab.counted && Number.isFinite(n) ? n : null
}

const subline = computed(() => header.value?.subline || '')

const now = ref(Date.now())
const clock = setInterval(() => { now.value = Date.now() }, 30000)
onBeforeUnmount(() => clearInterval(clock))
const updatedLabel = computed(() => {
  if (!updatedAt.value) return ''
  const min = Math.floor((now.value - updatedAt.value) / 60000)
  if (min < 1) return 'Updated just now'
  if (min < 60) return `Updated ${min}m ago`
  return `Updated ${Math.floor(min / 60)}h ago`
})

const picker = ref(null)
useTaskShortcuts({ p: () => picker.value?.open() })

// The three create dialogs live here, not in the tabs, so every tab's "New ▾"
// reaches every kind. Each prefills from the scope the strip names.
const { creating, openCreate } = usePipelineCreate()
const { flash } = useFlash()
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const createProject = computed(() => (/^\d+$/.test(carried.value) ? carried.value : ''))
function dialog(kind) {
  return computed({
    get: () => creating.value === kind,
    set: (v) => { if (v) openCreate(kind); else if (creating.value === kind) creating.value = null },
  })
}
const taskOpen = dialog('task')
const featureOpen = dialog('feature')
const defectOpen = dialog('defect')

function taskCreated(task) {
  invalidatePipelineCounts()
  // The Tasks feed already listens for a capture; a created task is one.
  window.dispatchEvent(new CustomEvent(TASK_CAPTURED_EVENT, { detail: { id: task.id } }))
  flash(`Task #${task.id} created`, 'success',
    { link: { to: `/tasks/${task.id}`, label: 'Open' } })
}

function featureDeclared(ledger) {
  invalidatePipelineCounts()
  if (!ledger?.id) return
  flash(`Feature #${ledger.id} declared`, 'success',
    { link: { to: `/features/${ledger.id}`, label: 'Open' } })
  router.push(`/features/${ledger.id}`)
}

function defectCreated({ defect, outcome }) {
  invalidatePipelineCounts()
  const where = defect.feature_id ? `on feature #${defect.feature_id}` : 'in triage'
  const verb = outcome === 'deduped' ? 'matched an open report' : 'reported'
  flash(`Defect #${defect.id} ${verb} ${where}`, 'success',
    { link: { to: `/defects/${defect.id}`, label: 'Open' } })
}

onMounted(() => {
  ensureProjects()
  if (onTab.value) restoreStored(isLegalScope)
})
watch(carried, (s) => { loadCounts(s); publishShellScope(s) }, { immediate: true })
onBeforeUnmount(() => publishShellScope(null))
// A tab writing a row is what makes its own count wrong, and the tab has no
// way to reach this header. It bumps a revision instead.
watch(countsRevision, () => loadCounts(carried.value))
usePipelineLive(() => loadCounts(carried.value))
</script>

<template>
  <div class="pl-shell">
    <header class="pl-head" data-testid="pipeline-header">
      <div class="pl-head__row">
        <div class="pl-head__title">
          <h1 class="page-title">Pipeline</h1>
          <ProjectPicker
            ref="picker"
            :scope="carried"
            :label="scopeLabel"
            :compact="!isMdUp"
            @select="pickScope"
          />
        </div>
        <span v-if="subline && isMdUp" class="pl-subline" data-testid="pipeline-subline">{{ subline }}</span>
        <span class="pl-spacer" />
        <span v-if="updatedLabel && isMdUp" class="pl-updated" data-testid="pipeline-updated">{{ updatedLabel }}</span>
        <!-- The tabs are siblings of this header, so a tab's create action
             teleports in here. Below 768px the same slot is the floating
             create button. -->
        <div id="pipeline-actions" class="pl-actions" />
      </div>

      <nav class="pl-tabs" aria-label="Pipeline sections" data-testid="pipeline-tabs">
        <router-link
          v-for="tab in TABS"
          :key="tab.path"
          :to="tabTo(tab)"
          class="pl-tab focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ 'is-active': isActive(tab) }"
          :aria-current="isActive(tab) ? 'page' : undefined"
          :data-testid="`pipeline-tab-${tab.key}`"
        >
          {{ tab.label }}
          <span
            v-if="tabCount(tab) != null"
            class="pl-tab__count"
            :data-testid="`pipeline-count-${tab.key}`"
          >{{ tabCount(tab) }}</span>
        </router-link>
      </nav>
    </header>

    <!-- A count nobody could fetch costs the header its numbers, never the
         page its rows. -->
    <p v-if="countsError" class="pl-counts-error" role="status" data-testid="pipeline-counts-error">
      Tab counts unavailable — {{ countsError }}
    </p>

    <!-- Kept alive rather than keyed by path, so a tab keeps its filters,
         selection and scroll across a switch. `include` rather than a `v-if`
         on the child: a KeepAlive whose single slot is conditionally empty
         corrupts Vue's activate/deactivate bookkeeping. -->
    <router-view v-slot="{ Component }">
      <KeepAlive :include="CACHED_TABS">
        <component :is="Component" :key="route.path" />
      </KeepAlive>
    </router-view>

    <template v-if="canWrite">
      <TaskComposerDialog
        v-model:open="taskOpen"
        :projects="projects"
        :project="createProject"
        @created="taskCreated"
      />
      <NewFeatureDialog v-model:open="featureOpen" :scope="createProject" @declared="featureDeclared" />
      <NewDefectDialog v-model:open="defectOpen" @created="defectCreated" />
    </template>
  </div>
</template>

<style scoped>
.pl-shell { display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.pl-head { border-bottom: 1px solid var(--color-border); }
.pl-head__row {
    display: flex; align-items: center; gap: 0.75rem; flex-wrap: nowrap;
    min-width: 0; padding-bottom: 0.6rem;
    /* Fixed, so a tab's teleported create button never changes the header's
       height — a shift there moves the scroller's anchored offset. */
    min-height: 3rem;
}
.pl-head__title { display: flex; align-items: center; gap: 0.75rem; min-width: 0; }
.pl-head__title .page-title { margin: 0; white-space: nowrap; }
.pl-subline {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 12.5px; color: var(--color-fg-muted);
}
.pl-spacer { flex: 1 1 auto; }
.pl-updated { font-size: 12px; color: var(--color-fg-faint); white-space: nowrap; }
.pl-actions { display: flex; align-items: center; gap: 0.5rem; flex: none; }

.pl-tabs { display: flex; gap: 1.25rem; margin-bottom: -1px; }
.pl-tab {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.5rem 0.1rem; font-size: 13.5px; font-weight: 500;
    color: var(--color-fg-muted); text-decoration: none;
    border-bottom: 2px solid transparent;
}
.pl-tab:hover { color: var(--color-fg); }
.pl-tab.is-active { color: var(--color-primary); border-bottom-color: var(--color-primary); }
.pl-tab__count {
    border-radius: var(--radius-md); background: var(--color-surface-3);
    padding: 0 0.35rem; font-size: 11px; color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.pl-counts-error { font-size: 0.8125rem; color: var(--color-fg-muted); }

@media (max-width: 767.98px) {
    .pl-head { border-bottom: 0; }
    .pl-head__title { flex-direction: column; align-items: flex-start; gap: 0; }
    .pl-tabs {
        gap: 0; margin: 0; padding: 3px; border-radius: var(--radius-xl);
        background: var(--color-surface-3);
    }
    .pl-tab {
        flex: 1; justify-content: center; min-height: 44px;
        border-bottom: 0; border-radius: var(--radius-lg); color: var(--color-fg-muted);
    }
    .pl-tab.is-active {
        background: var(--color-surface); color: var(--color-fg);
        box-shadow: 0 1px 2px rgb(0 0 0 / 0.08);
    }
    .pl-tab__count { display: none; }
    /* The create action floats bottom-right as a round "+" on a phone. */
    .pl-actions {
        /* Above the app's own quick-capture bubble, which owns the corner. */
        position: fixed; right: 1rem; bottom: calc(5rem + env(safe-area-inset-bottom));
        z-index: 30;
    }
    .pl-actions :deep(button) {
        width: 56px; height: 56px; border-radius: 999px; padding: 0;
        font-size: 0; gap: 0;
    }
    .pl-actions :deep(button.pcb-more) {
        width: 40px; height: 40px; align-self: center; margin-right: 0.5rem;
        border-radius: 999px;
    }
    .pl-actions :deep(.pcb) { flex-direction: row-reverse; }
    .pl-actions :deep(button.pcb-main) { border-radius: 999px; }
    .pl-actions :deep(button kbd), .pl-actions :deep(button span) { display: none; }
    .pl-actions :deep(button svg) { width: 20px; height: 20px; }
}
</style>
