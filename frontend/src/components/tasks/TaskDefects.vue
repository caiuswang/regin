<script setup>
// The defects blamed on the feature this task is — read, and reported.
//
// The list is the point. Reporting used to be write-only here: the outcome
// line was a local ref, so a reload left the operator with a defect they had
// been told was saved and could not find anywhere. A report that cannot be
// read back on the page that filed it is indistinguishable from a report that
// was dropped.
//
// Only a top-level task offers any of it. A child is a run of its parent, and
// a defect declared from a run's page would read as blamed on the run.
//
// The feature key comes from the server (`/api/tasks/<id>` → `feature_key`),
// never composed from the id here: a task linked to another tracker is a run
// of THAT issue, and `regin:<id>` would file its defects under a key the
// ledger counts against nobody.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import Textarea from '../ui/Textarea.vue'
import DefectRow from '../defects/DefectRow.vue'

const props = defineProps({
  task: { type: Object, required: true },
  featureKey: { type: String, default: '' },
  canWrite: { type: Boolean, default: false },
})

const open = ref(false)
const symptom = ref('')
const busy = ref(false)
const failed = ref('')
const outcome = ref('')
const items = ref([])
const loading = ref(true)
const loadError = ref('')

const isFeature = computed(() => props.task.parent_id === null)
const canSubmit = computed(() => symptom.value.trim().length > 0 && !busy.value)
const scopeHref = computed(
  () => `/defects?feature=${encodeURIComponent(props.featureKey)}`)

// Re-read rather than patch: re-attributing a defect moves it off this
// feature, and splicing the row back would leave it listed under "every defect
// blamed on this feature" when it no longer is.
async function load() {
  if (!isFeature.value || !props.featureKey) {
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(
      `/defects?feature=${encodeURIComponent(props.featureKey)}`)
    items.value = body.items || []
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(() => [props.task.id, props.featureKey], load, { immediate: true })

// `opened` and `deduped` are two different facts and say so: the second means
// the ledger already held this complaint, and reading it as a fresh report is
// how a defect goes unreported. Both re-read the list, because both changed it.
async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  failed.value = ''
  const body = await api.post('/defects', {
    symptom: symptom.value.trim(), feature_key: props.featureKey,
  })
  busy.value = false
  if (body?.ok === false || !body?.defect) {
    failed.value = body?.msg || 'the report was refused'
    return
  }
  outcome.value = body.outcome === 'deduped'
    ? `#${body.defect.id} already tracked this symptom — deduped, not reopened.`
    : `#${body.defect.id} opened against ${props.featureKey}.`
  symptom.value = ''
  open.value = false
  await load()
}

</script>

<template>
  <Card v-if="isFeature && featureKey">
    <div class="td-head">
      <h2 class="card-header">Defects</h2>
      <router-link class="td-key" :to="scopeHref">{{ featureKey }}</router-link>
      <span class="td-spacer" />
      <Button
        v-if="canWrite && !open"
        variant="ghost" size="sm"
        data-testid="task-report-defect"
        :aria-label="`Report a defect against ${featureKey}`"
        @click="open = true"
      >
        <Icon name="alert-triangle" :size="12" />
        Report defect
      </Button>
    </div>

    <form v-if="open" class="td-form" @submit.prevent="submit">
      <label class="td-label" :for="`rd-symptom-${task.id}`">
        What went wrong — blamed on {{ featureKey }}
      </label>
      <Textarea
        :id="`rd-symptom-${task.id}`"
        v-model="symptom" :rows="2" :disabled="busy"
        placeholder="the symptom, in your words"
        data-testid="task-defect-symptom"
      />
      <div class="td-actions">
        <Button
          type="submit" variant="secondary" size="sm" :disabled="!canSubmit"
          data-testid="task-defect-submit"
        >{{ busy ? 'Reporting…' : 'Report' }}</Button>
        <Button
          type="button" variant="ghost" size="sm" :disabled="busy"
          @click="open = false"
        >Cancel</Button>
      </div>
    </form>

    <p v-if="outcome" class="td-note" role="status" data-testid="task-defect-result">
      {{ outcome }}
    </p>
    <p v-if="failed" class="td-fail" role="status" data-testid="task-defect-error">
      {{ failed }}
    </p>

    <p v-if="loading" class="td-note">Loading…</p>
    <p v-else-if="loadError" class="td-fail" role="alert">{{ loadError }}</p>
    <p v-else-if="!items.length" class="td-note" data-testid="task-defects-empty">
      No defects are blamed on this feature.
    </p>
    <ul v-else class="td-list" data-testid="task-defects-list">
      <DefectRow
        v-for="defect in items"
        :key="defect.id"
        :defect="defect"
        :can-write="canWrite"
        @changed="load()"
      />
    </ul>
  </Card>
</template>

<style scoped>
.td-head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    margin-bottom: 0.6rem;
}
.td-key {
    font-family: var(--font-mono, monospace); font-size: 11.5px;
    color: var(--color-primary); text-decoration: none;
}
.td-key:hover { text-decoration: underline; }
.td-spacer { flex: 1 1 auto; }
.td-form { display: flex; flex-direction: column; gap: 0.35rem; }
.td-label { font-size: 11.5px; color: var(--color-fg-muted); }
.td-actions { display: flex; gap: 0.4rem; }
.td-note {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-fg-muted);
}
.td-fail {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
.td-list {
    display: flex; flex-direction: column; gap: 0.6rem;
    margin: 0.6rem 0 0; padding: 0; list-style: none;
}
</style>
