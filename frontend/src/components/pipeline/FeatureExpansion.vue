<script setup>
// An expanded feature row (1c): its active tasks, the defects blamed on it and
// the fix tasks of its current round. Read on expand, never on the page load.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import TaskStatusIcon from '../tasks/TaskStatusIcon.vue'
import FeatureActiveTasks from './FeatureActiveTasks.vue'
import { defectRef } from '../../utils/pipelineFormat'

const props = defineProps({
  feature: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})

const defects = ref([])
const tasks = ref(new Map())
const loaded = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const [d, t] = await Promise.all([
      api.get(`/defects?feature_id=${props.feature.id}&with_fix_state=1&size=50`),
      props.compact ? null : api.get(`/tasks?feature_id=${props.feature.id}&size=100`),
    ])
    defects.value = d.items || []
    tasks.value = new Map((t?.items || []).map(task => [task.id, task]))
  } catch (e) {
    error.value = e?.message || String(e)
  } finally {
    loaded.value = true
  }
})

const open = computed(() => defects.value.filter(d => d.status === 'open'))
const shown = computed(() => (props.compact ? open.value : defects.value))

function stateOf(d) {
  if (d.status !== 'open') return { text: d.status, tone: 'muted' }
  if (d.fix_state === 'running') return { text: 'open · fix running', tone: 'agent' }
  if (d.fix_state && !['done', 'failed', 'archived'].includes(d.fix_state)) {
    return { text: `open · fix ${d.fix_state}`, tone: 'warning' }
  }
  return { text: 'open', tone: 'danger' }
}

// The fix tasks carrying this feature's open defects, one row each.
const fixes = computed(() => {
  const seen = new Map()
  for (const d of open.value) {
    if (!d.fix_task_id || seen.has(d.fix_task_id)) continue
    const task = tasks.value.get(d.fix_task_id)
    seen.set(d.fix_task_id, {
      id: d.fix_task_id,
      title: task?.title || `task #${d.fix_task_id}`,
      status: task?.status || d.fix_state || 'idea',
    })
  }
  return [...seen.values()]
})
</script>

<template>
  <div class="fx" :class="{ 'fx--compact': compact }" :data-testid="`feature-expansion-${feature.id}`">
    <FeatureActiveTasks v-if="!compact" :feature-id="feature.id" />
    <p v-if="!loaded" class="fx-muted">Loading…</p>
    <p v-else-if="error" class="fx-muted" role="alert">{{ error }}</p>
    <template v-else>
      <div class="fx-col">
        <h3 v-if="!compact" class="fx-label">Blamed defects · {{ open.length }} open</h3>
        <p v-if="!shown.length" class="fx-muted">No defect is blamed on this feature.</p>
        <ul class="fx-list">
          <li v-for="d in shown" :key="d.id" class="fx-item" :data-testid="`feature-defect-${d.id}`">
            <router-link :to="`/defects/${d.id}`" class="fx-id focus-visible:outline-2 focus-visible:outline-blue-500">{{ defectRef(d.id) }}</router-link>
            <span class="fx-text">{{ d.symptom }}</span>
            <span class="fx-state" :class="`text-tone-${stateOf(d).tone}`">{{ stateOf(d).text }}</span>
          </li>
        </ul>
      </div>
      <div v-if="!compact" class="fx-col">
        <h3 class="fx-label">Fix tasks · round {{ feature.rounds }}</h3>
        <p v-if="!fixes.length" class="fx-muted">No fix task is carrying an open defect.</p>
        <ul class="fx-list">
          <li v-for="t in fixes" :key="t.id" class="fx-item" :data-testid="`feature-fix-${t.id}`">
            <TaskStatusIcon :status="t.status" />
            <span class="fx-tid">#{{ t.id }}</span>
            <router-link :to="`/tasks/${t.id}`" class="fx-text fx-link focus-visible:outline-2 focus-visible:outline-blue-500">{{ t.title }}</router-link>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>

<style scoped>
.fx { display: flex; flex-direction: column; gap: 0.9rem; padding: 0.25rem 0 0.5rem; }
.fx--compact { display: block; padding: 0; }
.fx-col { min-width: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.fx-label {
    font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.fx-list { display: flex; flex-direction: column; gap: 0.35rem; }
.fx-item {
    display: flex; align-items: center; gap: 0.5rem; min-width: 0;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); padding: 0.4rem 0.6rem; font-size: 12.5px;
}
.fx--compact .fx-item { border: 0; border-top: 1px solid var(--color-border-subtle); border-radius: 0; padding: 0.55rem 0; background: none; }
.fx-id { font-family: var(--font-mono, monospace); font-weight: 700; color: var(--color-fg); text-decoration: none; flex: none; }
.fx-tid { color: var(--color-fg-faint); flex: none; }
.fx-text { flex: 1; min-width: 0; text-wrap: pretty; color: var(--color-fg); }
.fx--compact .fx-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fx-link { text-decoration: none; }
.fx-link:hover { text-decoration: underline; }
.fx-state { flex: none; font-size: 11.5px; font-weight: 500; }
.fx-muted { font-size: 12px; color: var(--color-fg-muted); }
</style>
