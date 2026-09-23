<script setup>
// One project's panel — Linear's project overview, read off regin's rows: the
// derived status and a stage-segmented progress bar up top, the figures as
// links into the three tabs under this project's scope, then the features by
// stage and what is open. `/projects/none` is a real panel: the rows filed
// under no project have to be reachable from somewhere.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Badge from '../components/Badge.vue'
import Card from '../components/Card.vue'
import StatCard from '../components/StatCard.vue'
import ProjectFeatureGroups from '../components/projects/ProjectFeatureGroups.vue'
import ProjectOpenWork from '../components/projects/ProjectOpenWork.vue'
import ProjectProgress from '../components/projects/ProjectProgress.vue'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { OPEN_TASKS_HINT, STATUS_COLOR, STATUS_LABEL, openTasks, projectName }
  from '../utils/projectFormat'

const route = useRoute()
const publish = usePipelineHeaderPublisher()

const data = ref(null)
const loading = ref(true)
const loadError = ref('')

const project = computed(() => data.value?.project || null)
const scope = computed(() => project.value?.scope ?? String(route.params.id))

function tabHref(path, extra = '') {
  return `${path}?project=${encodeURIComponent(scope.value)}${extra}`
}

const tiles = computed(() => {
  const p = project.value
  if (!p) return []
  return [
    { key: 'features', label: 'Features', value: p.features.total, href: tabHref('/features') },
    { key: 'tasks', label: 'Open tasks', value: openTasks(p.tasks),
      suffix: ` / ${p.tasks.total}`, href: tabHref('/tasks'), hint: OPEN_TASKS_HINT },
    { key: 'defects', label: 'Open defects', value: p.defects.open,
      suffix: ` / ${p.defects.total}`, href: tabHref('/defects') },
    { key: 'escaped', label: 'Escaped', value: p.features.by_stage.escaped || 0,
      href: tabHref('/features', '&stage=escaped') },
  ]
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await api.get(`/pipeline/projects/${encodeURIComponent(route.params.id)}`)
    publish({ subtitle: `${projectName(data.value.project)} — every figure below opens the tab it counts, under this project.` })
  } catch (e) {
    data.value = null
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(() => route.params.id, id => { if (id) load() }, { immediate: true })
</script>

<template>
  <div class="space-y-4" data-testid="project-panel">
    <p class="ppv-back">
      <router-link to="/projects">&larr; every project</router-link>
    </p>

    <div v-if="loading && !data" class="empty-state">Loading project…</div>
    <p v-else-if="loadError" class="ppv-error" role="alert" data-testid="project-error">
      This project could not be read — {{ loadError }}
    </p>

    <template v-else-if="project">
      <Card>
        <div class="ppv-head">
          <div class="ppv-head__text">
            <h2 class="ppv-title" data-testid="project-name">{{ projectName(project) }}</h2>
            <p v-if="project.description" class="ppv-desc">{{ project.description }}</p>
            <code v-if="project.path" class="cell-code">{{ project.path }}</code>
            <p v-else-if="project.scope === 'none'" class="ppv-desc">
              Features and tasks declared in no project, and defects attributed to no feature.
            </p>
          </div>
          <Badge
            :color="STATUS_COLOR[project.status] || 'gray'"
            :label="STATUS_LABEL[project.status] || project.status"
            data-testid="project-status"
          />
        </div>
        <ProjectProgress class="ppv-progress" :progress="project.progress" :by-stage="project.features.by_stage" />
      </Card>

      <div class="ppv-tiles">
        <StatCard v-for="t in tiles" :key="t.key" :href="t.href" :title="t.hint" :data-testid="`project-tile-${t.key}`">
          <div class="stat-label">{{ t.label }}</div>
          <div class="stat-value">
            {{ t.value }}<span v-if="t.suffix" class="stat-value-suffix">{{ t.suffix }}</span>
          </div>
        </StatCard>
      </div>

      <h2 class="section-heading">Features by stage</h2>
      <ProjectFeatureGroups :features="data.features" :scope="project.scope" />

      <ProjectOpenWork
        :defects="data.open_defects"
        :defects-open="project.defects.open"
        :tasks="data.recent_tasks"
        :scope="project.scope"
      />
    </template>
  </div>
</template>

<style scoped>
.ppv-back { font-size: 0.8125rem; }
.ppv-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.ppv-head__text { min-width: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.ppv-title { font-size: 1.125rem; font-weight: 600; margin: 0; overflow-wrap: anywhere; }
.ppv-desc { margin: 0; font-size: 0.875rem; color: var(--color-text-muted, #6b7280); }
.ppv-progress { margin-top: 1rem; }
.ppv-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.75rem; }
.ppv-error { font-size: 0.875rem; color: var(--color-red-700, #b91c1c); }
</style>
