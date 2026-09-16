<script setup>
// The pipeline's one workbench. Tasks, features and defects are three readings
// of a single loop — what is queued, what was claimed done, what came back —
// and three sidebar entries made that loop look like three unrelated tables.
// One entry, one header, and a tab strip that says the three belong together.
//
// The child URLs are unchanged (`/tasks`, `/features`, `/defects`): the routes
// are absolute children of this shell, so every existing link, bookmark and
// CLI-printed URL still lands where it always did.
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import Select from '../components/ui/Select.vue'
import { usePipelineHeader } from '../composables/usePipelineHeader'
import { usePipelineCounts } from '../composables/usePipelineCounts'
import { usePipelineTabState } from '../composables/usePipelineTabState'
import { usePipelineScope, scopeQuery, SCOPE_ALL, SCOPE_NONE }
  from '../composables/usePipelineScope'
import { useProjects } from '../composables/useProjects'

const route = useRoute()

const TABS = [
  { path: '/tasks', key: 'tasks', label: 'Tasks',
    blurb: 'Nothing runs until someone arms it — and an armed task waits for every dependency it names.' },
  { path: '/features', key: 'features', label: 'Features',
    blurb: 'Every feature regin knows. Declaring one is the only way in.' },
  { path: '/defects', key: 'defects', label: 'Defects',
    blurb: 'Every defect regin holds, newest first — attributed or not.' },
]

function isActive(tab) {
  return route.path === tab.path || route.path.startsWith(`${tab.path}/`)
}

const active = computed(() => TABS.find(isActive) || null)
// A detail route is a page of its own, not a tab: it keeps the strip above it
// but must never be cached, or two ids would share one mount.
const CACHED_TABS = ['TasksView', 'FeaturesView', 'DefectsView']

const header = usePipelineHeader(() => route.path)
const { scope, setScope, restoreStored } = usePipelineScope()
const { counts, error: countsError, load: loadCounts } = usePipelineCounts()
const { queryFor } = usePipelineTabState(p => TABS.some(t => t.path === p))
const { items: projects, ensure: ensureProjects, error: projectsError } = useProjects()

const projectOptions = computed(() => [
  { value: SCOPE_ALL, label: 'All projects' },
  { value: SCOPE_NONE, label: 'No project' },
  ...projects.value.map(p => ({ value: String(p.id), label: p.name })),
])

// The registry can fail while the scope still holds. Naming the raw id beats
// dropping the chip, which would show a filtered list under an "All" control.
const unknownScope = computed(() =>
  scope.value !== SCOPE_ALL && scope.value !== SCOPE_NONE
  && !projects.value.some(p => String(p.id) === scope.value))

function tabTo(tab) {
  return { path: tab.path, query: queryFor(tab.path, scopeQuery(scope.value)) }
}

function tabCount(tab) {
  const n = counts.value?.[tab.key]
  return Number.isFinite(n) ? n : null
}

// The child's own sentence wins when it has published one — a defect scope and
// a task filter describe themselves better than a fixed caption can — and the
// tab's blurb is what stands there until it does.
const subtitle = computed(() => header.value?.subtitle || active.value?.blurb || '')

onMounted(() => {
  ensureProjects()
  restoreStored(v => v === SCOPE_NONE || /^\d+$/.test(v))
})
watch(scope, s => loadCounts(s), { immediate: true })
</script>

<template>
  <div class="space-y-4">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Engineering · Pipeline</div>
        <h1 class="page-title">Pipeline</h1>
        <p class="page-subtitle">{{ subtitle }}</p>
      </div>
      <!-- The tabs are siblings of this header, not descendants, so a tab's
           own controls cannot be passed down as slot content. They teleport
           in here instead, which keeps every page's actions in the one place
           the eye already looks for them. -->
      <div id="pipeline-actions" class="page-actions"></div>
    </header>

    <!-- Above the strip, not inside it: the project is the one axis that
         carries from tab to tab, so it cannot belong to any one of them. -->
    <div class="pl-scope">
      <Select
        :model-value="scope"
        :options="projectOptions"
        aria-label="Project scope"
        data-testid="pipeline-project"
        @update:model-value="setScope"
      />
      <span v-if="unknownScope" class="pl-scope__raw" data-testid="pipeline-project-raw">
        project #{{ scope }}{{ projectsError ? ' — the project list could not be read' : '' }}
      </span>
    </div>

    <nav class="segmented mb-4" aria-label="Pipeline sections" data-testid="pipeline-tabs">
      <router-link
        v-for="tab in TABS"
        :key="tab.path"
        :to="tabTo(tab)"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': isActive(tab) }"
        :aria-current="isActive(tab) ? 'page' : undefined"
        :data-testid="`pipeline-tab-${tab.label.toLowerCase()}`"
      >
        {{ tab.label }}
        <span
          v-if="tabCount(tab) != null"
          class="segmented-item__count"
          :data-testid="`pipeline-count-${tab.key}`"
        >{{ tabCount(tab) }}</span>
      </router-link>
    </nav>

    <!-- A count nobody could fetch costs the header its numbers, never the
         page its rows — so this is a line above the list, not a takeover. -->
    <p v-if="countsError" class="pl-counts-error" role="status" data-testid="pipeline-counts-error">
      Tab counts unavailable — {{ countsError }}
    </p>

    <!-- Kept alive rather than keyed by path: `:key="$route.path"` remounted
         every tab on every switch, which is what dropped its filters,
         selection, open groups and scroll.
         `include` rather than a `v-if` on the child: only the three tabs are
         worth caching (a detail must remount per id), and a KeepAlive whose
         single slot is conditionally empty corrupts Vue's activate/deactivate
         bookkeeping. A tab's path is constant, so keying on it caches one
         entry per tab and still gives each detail id its own mount. -->
    <router-view v-slot="{ Component }">
      <KeepAlive :include="CACHED_TABS">
        <component :is="Component" :key="route.path" />
      </KeepAlive>
    </router-view>
  </div>
</template>

<style scoped>
.page-header { align-items: flex-start; }
.page-subtitle { max-width: 56rem; }

.pl-scope {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.pl-scope__raw {
    font-size: 0.8125rem;
    color: var(--color-text-muted, #6b7280);
}

.pl-counts-error {
    font-size: 0.8125rem;
    color: var(--color-text-muted, #6b7280);
}
</style>
