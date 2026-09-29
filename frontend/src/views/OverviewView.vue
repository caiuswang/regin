<script setup>
// The Pipeline's landing tab (designs 1a, 1d, 2a — the Quiet loop): the loop,
// what to run next, what needs a decision, and the projects — all from one
// `GET /api/pipeline/overview?project=` read under the shell's scope.
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Button from '../components/ui/Button.vue'
import OverviewLoop from '../components/pipeline/OverviewLoop.vue'
import RunNextPanel from '../components/pipeline/RunNextPanel.vue'
import NeedsDecisionPanel from '../components/pipeline/NeedsDecisionPanel.vue'
import ProjectsPanel from '../components/pipeline/ProjectsPanel.vue'
import ProjectScopePanel from '../components/pipeline/ProjectScopePanel.vue'
import TriageSheet from '../components/pipeline/TriageSheet.vue'
import PipelineCreateButton from '../components/pipeline/PipelineCreateButton.vue'
import { usePipelineCreate } from '../composables/usePipelineCreate'
import { usePipelineTabActive } from '../composables/usePipelineHeader'
import { invalidatePipelineCounts, markPipelineRead, usePipelineCounts } from '../composables/usePipelineCounts'
import { scopeOf, scopeQuery, usePipelineScope, SCOPE_ALL } from '../composables/usePipelineScope'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import { useBreakpoint } from '../composables/useBreakpoint'
import { usePipelineLive } from '../composables/usePipelineEvents'

const route = useRoute()
const isCurrentTab = usePipelineTabActive()
const { setScope } = usePipelineScope()
const { revision } = usePipelineCounts()
const { isMdUp } = useBreakpoint()

const scope = computed(() => scopeOf(route.query))
const data = ref(null)
const error = ref('')
const missing = ref(false)
const loading = ref(false)
let issued = 0

async function load() {
  const mine = ++issued
  loading.value = true
  try {
    const qs = scope.value !== SCOPE_ALL ? `?project=${encodeURIComponent(scope.value)}` : ''
    const body = await api.get(`/pipeline/overview${qs}`)
    if (mine !== issued) return
    data.value = body
    error.value = ''
    missing.value = false
    markPipelineRead()
  } catch (e) {
    if (mine !== issued) return
    missing.value = e?.status === 404
    // The last good read belongs to the scope the operator just left; kept
    // under the new chip it would read as that scope's panels.
    if (missing.value) data.value = null
    error.value = e?.message || String(e)
  } finally {
    if (mine === issued) loading.value = false
  }
}

function changed() {
  invalidatePipelineCounts()
}

const tasksTo = computed(() => ({ path: '/tasks', query: scopeQuery(scope.value) }))
function featuresTo(triageId, stage) {
  const query = { ...scopeQuery(scope.value) }
  if (triageId != null) query.triage = String(triageId)
  if (stage) query.stage = stage
  return { path: '/features', query }
}

const { openCreate } = usePipelineCreate()
const triaging = ref(false)
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const picker = () => document.querySelector('[data-testid="pipeline-project"]')

useTaskShortcuts({
  c: () => { if (canWrite.value) openCreate('task') },
  // The Overview has no search of its own; the chip's picker owns it.
  '/': () => picker()?.click(),
})

watch(() => (route.path === '/overview' ? scope.value : null), (s) => { if (s != null) load() })
// Every write the tabs make bumps this; the Overview's panels are the ones it
// moves most.
watch(revision, () => { if (route.path === '/overview') load() })

// `mounted` then `activated` both fire on the first arrival inside KeepAlive,
// so the guard is armed by the activation itself.
let arrived = false
usePipelineLive(load)
onMounted(load)
onActivated(() => { if (arrived) load(); arrived = true })
</script>

<template>
  <div class="ov" data-testid="overview">
    <Teleport v-if="isCurrentTab && canWrite" defer to="#pipeline-actions">
      <PipelineCreateButton kind="task" testid="task-new" />
    </Teleport>

    <div v-if="error && !data" class="ov-error" role="alert" data-testid="overview-error">
      <p v-if="missing">No project #{{ scope }} — nothing is filed under that id.</p>
      <p v-else>The overview could not be read — {{ error }}</p>
      <Button v-if="missing" size="sm" @click="setScope(SCOPE_ALL)">Show all projects</Button>
      <Button v-else size="sm" @click="load">Retry</Button>
    </div>
    <p v-else-if="!data" class="ov-loading">Loading…</p>

    <template v-else>
      <p v-if="error" class="ov-stale" role="status">Showing the last good read — {{ error }}</p>
      <OverviewLoop :loop="data.loop" :ready="data.run_next.ready" :compact="!isMdUp" :scope="scopeQuery(scope)" />

      <ProjectScopePanel
        v-if="data.project_head && isMdUp"
        :head="data.project_head"
        :features="data.project_features"
        :features-to="featuresTo()"
        @all="setScope(SCOPE_ALL)"
      />

      <div class="ov-grid" :class="{ 'is-busy': loading }">
        <RunNextPanel :run-next="data.run_next" :running="data.running" :compact="!isMdUp" :tasks-to="tasksTo" @ran="changed" />
        <NeedsDecisionPanel
          :decision="data.needs_decision"
          :compact="!isMdUp"
          :features-to="featuresTo"
          @triage="triaging = true"
        />
      </div>

      <ProjectsPanel
        v-if="data.projects"
        :panel="data.projects"
        :compact="!isMdUp"
        @pick="setScope"
        @all="picker()?.click()"
      />
    </template>

    <TriageSheet v-model:open="triaging" :scope="scope" @changed="changed" />
  </div>
</template>

<style scoped>
.ov { display: flex; flex-direction: column; gap: 1.25rem; min-width: 0; }
.ov-grid {
    display: grid; gap: 1.25rem; align-items: start;
    grid-template-columns: repeat(2, minmax(0, 1fr));
}
.ov-grid.is-busy { opacity: 0.7; }
.ov-loading, .ov-stale { font-size: 12.5px; color: var(--color-fg-muted); }
.ov-error {
    display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    padding: 1rem 1.1rem; font-size: 13px; color: var(--color-fg-muted);
}

@media (max-width: 1023.98px) {
    .ov-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 767.98px) {
    .ov { gap: 0.9rem; padding-bottom: 5rem; }
    .ov-grid { gap: 0.9rem; }
}
</style>
