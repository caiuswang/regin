<script setup>
// Every project, the way Linear lists them: a name, a derived status and a
// progress bar per row. A project is a registered repo; nothing here is
// stored, every figure is a rollup the tab strip would report under the same
// `?project=`.
import { onActivated, ref } from 'vue'
import api from '../api'
import Badge from '../components/Badge.vue'
import Card from '../components/Card.vue'
import ProjectProgress from '../components/projects/ProjectProgress.vue'
import { usePipelineHeaderPublisher, usePipelineTabActive } from '../composables/usePipelineHeader'
import { OPEN_TASKS_HINT, STATUS_COLOR, STATUS_LABEL, openTasks, projectName }
  from '../utils/projectFormat'

const publish = usePipelineHeaderPublisher()
const isCurrentTab = usePipelineTabActive()
const items = ref([])
const loading = ref(true)
const error = ref('')

async function load() {
  error.value = ''
  try {
    const body = await api.get('/pipeline/projects')
    items.value = body.items || []
    const registered = items.value.filter(p => p.registered).length
    // A fetch that lands after the reader has opened a panel must not take the
    // panel's caption over.
    if (isCurrentTab.value) publish({ subtitle: `${registered} registered project${registered === 1 ? '' : 's'} — progress is read off the stages of the features filed under each.` })
  } catch (e) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

// Kept alive by the shell, so a write made on another tab only shows here if
// the list is re-read when the tab comes back.
// `onActivated` also fires on the first mount, so it is the only load hook.
onActivated(load)

function isQuiet(p) {
  return p.features.total === 0 && p.tasks.total === 0 && p.defects.total === 0
}
</script>

<template>
  <div class="space-y-4" data-testid="projects-view">
    <div v-if="loading" class="empty-state">Loading projects…</div>
    <p v-else-if="error" class="pv-error" role="alert" data-testid="projects-error">
      Projects could not be read — {{ error }}
    </p>
    <Card v-else :no-padding="true">
      <div class="overflow-x-auto">
        <table class="tbl" data-testid="projects-table">
          <thead>
            <tr>
              <th>Project</th>
              <th style="width: 8rem">Status</th>
              <th style="width: 18rem">Progress</th>
              <th class="num" style="width: 6rem">Features</th>
              <th class="num" style="width: 6rem" :title="OPEN_TASKS_HINT">Open tasks</th>
              <th class="num" style="width: 7rem">Open defects</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in items"
              :key="p.scope"
              :class="{ 'pv-quiet': isQuiet(p) }"
              :data-testid="`project-row-${p.scope}`"
            >
              <td>
                <router-link
                  :to="`/projects/${p.scope}`"
                  class="table-link font-medium focus-visible:outline-2 focus-visible:outline-blue-500"
                >{{ projectName(p) }}</router-link>
                <div v-if="p.description" class="pv-desc">{{ p.description }}</div>
                <div v-else-if="!p.registered && p.scope !== 'none'" class="pv-desc">
                  not in the registry — rows still name this id
                </div>
              </td>
              <td><Badge :color="STATUS_COLOR[p.status] || 'gray'" :label="STATUS_LABEL[p.status] || p.status" /></td>
              <td><ProjectProgress :progress="p.progress" :by-stage="p.features.by_stage" compact /></td>
              <td class="num">{{ p.features.total }}</td>
              <td class="num">{{ openTasks(p.tasks) }}</td>
              <td class="num" :class="{ 'pv-hot': p.defects.open > 0 }">{{ p.defects.open }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pv-desc {
    font-size: 0.75rem;
    color: var(--color-text-muted, #6b7280);
    margin-top: 0.125rem;
    max-width: 32rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.pv-quiet { opacity: 0.6; }
.pv-hot { color: var(--color-amber-700, #b45309); font-weight: 600; }
.pv-error { font-size: 0.875rem; color: var(--color-red-700, #b91c1c); }
</style>
