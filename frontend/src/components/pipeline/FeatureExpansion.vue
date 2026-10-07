<script setup>
// An expanded feature row (1c): its active tasks and the defects blamed on it.
// Fix tasks get no section of their own: a live one is already an active task,
// and each defect row carries its fix's state. Read on expand, never on the page load.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import FeatureActiveTasks from './FeatureActiveTasks.vue'
import PlGlyph from './PlGlyph.vue'
import { defectRef } from '../../utils/pipelineFormat'

const props = defineProps({
  feature: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})

const defects = ref([])
const loaded = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const d = await api.get(`/defects?feature_id=${props.feature.id}&with_fix_state=1&size=50`)
    defects.value = d.items || []
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
</script>

<template>
  <div class="fx" :class="{ 'fx--compact': compact }" data-pl-part="expansion" :data-pl-compact="compact ? '' : undefined" :data-testid="`feature-expansion-${feature.id}`">
    <FeatureActiveTasks v-if="!compact" :feature-id="feature.id" />
    <p v-if="!loaded" class="fx-muted" data-pl-part="note">Loading…</p>
    <p v-else-if="error" class="fx-muted" role="alert" data-pl-part="note">{{ error }}</p>
    <template v-else>
      <div class="fx-col">
        <h3 v-if="!compact" class="fx-label" data-pl-part="section-label"><PlGlyph kind="defect.wild" />Blamed defects · {{ open.length }} open</h3>
        <p v-if="!shown.length" class="fx-muted" data-pl-part="note">No defect is blamed on this feature.</p>
        <ul class="fx-list">
          <li v-for="d in shown" :key="d.id" class="fx-item" :data-testid="`feature-defect-${d.id}`" data-pl-part="sub-row">
            <PlGlyph kind="defect.wild" />
            <router-link :to="`/defects/${d.id}`" class="fx-id focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="row-id">{{ defectRef(d.id) }}</router-link>
            <span class="fx-text">{{ d.symptom }}</span>
            <span class="fx-state" :class="`text-tone-${stateOf(d).tone}`" data-pl-part="state" :data-tone="stateOf(d).tone">{{ stateOf(d).text }}</span>
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
[data-theme="dark"] .fx-item { background: var(--color-slate-50); border-color: transparent; }
.fx--compact .fx-item { border: 0; border-top: 1px solid var(--color-border-subtle); border-radius: 0; padding: 0.55rem 0; background: none; }
.fx-id { font-family: var(--font-mono, monospace); font-weight: 700; color: var(--color-fg); text-decoration: none; flex: none; }
.fx-text { flex: 1; min-width: 0; text-wrap: pretty; color: var(--color-fg); }
.fx--compact .fx-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fx-state { flex: none; font-size: 11.5px; font-weight: 500; }
.fx-muted { font-size: 12px; color: var(--color-fg-muted); }
</style>
