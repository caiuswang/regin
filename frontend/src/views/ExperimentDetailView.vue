<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Breadcrumb from '../components/Breadcrumb.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'

const route = useRoute()
const router = useRouter()
const { flash } = useFlash()
const { confirm } = useConfirm()
const data = ref(null)
const loading = ref(true)
const editing = ref(false)
const editName = ref('')
const editSections = ref([])
const editErrors = ref({})
const processing = ref(false)

async function load() {
  data.value = await api.get(`/experiments/${route.params.id}`)
  editName.value = data.value.exp.name
  editSections.value = [...data.value.exp.sections]
  loading.value = false
}

onMounted(load)

async function saveEdit() {
  editErrors.value = {}
  if (!editName.value.trim()) editErrors.value.name = 'Name is required'
  if (!editSections.value.length) editErrors.value.sections = 'Select at least one section'
  if (Object.keys(editErrors.value).length) return
  const result = await api.post(`/experiments/${route.params.id}/edit`, {
    name: editName.value,
    sections: editSections.value,
  })
  if (!result.ok) { flash(result.msg || 'Failed to save', 'error'); return }
  flash(result.msg)
  editing.value = false
  await load()
}

async function activate() {
  processing.value = true
  data.value.exp.active = true
  data.value.exp.activated_at = new Date().toISOString().replace('T', ' ').slice(0, 19)
  const result = await api.post(`/experiments/${route.params.id}/activate`)
  processing.value = false
  if (!result.ok) {
    flash(result.msg || 'Failed to activate', 'error')
    await load()
    return
  }
  flash(result.msg)
  await load()
}

async function deactivate() {
  processing.value = true
  data.value.exp.active = false
  data.value.exp.activated_at = null
  const result = await api.post(`/experiments/${route.params.id}/deactivate`)
  processing.value = false
  if (!result.ok) {
    flash(result.msg || 'Failed to deactivate', 'error')
    await load()
    return
  }
  flash(result.msg)
  await load()
}

async function deleteExp() {
  const ok = await confirm('Delete experiment', `Delete experiment ${data.value.exp.name}?`, true)
  if (!ok) return
  const result = await api.post(`/experiments/${route.params.id}/delete`)
  if (!result.ok) { flash(result.msg || 'Failed to delete', 'error'); return }
  flash(result.msg)
  router.push('/experiments')
}

function fmtRate(rate) {
  return rate != null ? (rate * 100).toFixed(1) + '%' : '—'
}

function deltaColor(bRate, eRate) {
  if (bRate == null || eRate == null) return null
  const d = (eRate - bRate) * 100
  if (d > 0) return 'red'
  if (d < 0) return 'green'
  return null
}

function deltaLabel(bRate, eRate) {
  if (bRate == null || eRate == null) return '—'
  const d = (eRate - bRate) * 100
  return (d > 0 ? '+' : '') + d.toFixed(1) + 'pp'
}

function ruleRate(checks, fired) {
  return checks ? fired / checks : null
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading experiment…</div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Experiments', to: '/experiments' },
      { label: data.exp.name, to: null },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Experiment</div>
        <h1 class="page-title">
          {{ data.exp.name }}
          <Badge :color="data.exp.active ? 'green' : 'gray'" :label="data.exp.active ? 'active' : 'idle'" />
        </h1>
        <p class="page-subtitle">
          Pattern <router-link :to="`/patterns/${data.exp.pattern_slug}`"
            class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
            <code class="cell-code">{{ data.exp.pattern_slug }}</code>
          </router-link>
          · created {{ data.exp.created_at }}
          <template v-if="data.exp.activated_at">· last activated {{ data.exp.activated_at }}</template>
        </p>
      </div>
      <div class="page-actions">
        <button v-if="data.exp.active"
          type="button"
          class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="processing" @click="deactivate">
          {{ processing ? 'Deactivating…' : 'Deactivate' }}
        </button>
        <button v-else
          type="button"
          class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="processing" @click="activate">
          {{ processing ? 'Activating…' : 'Activate' }}
        </button>
        <button type="button" class="btn btn-danger focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="processing" @click="deleteExp">Delete</button>
      </div>
    </header>

    <Card>
      <h2 class="card-header">Concealed sections</h2>
      <div class="flex flex-wrap gap-1.5">
        <code v-for="s in data.exp.sections" :key="s" class="cell-code">{{ s }}</code>
      </div>
    </Card>

    <Card>
      <h2 class="card-header">Baseline vs experiment</h2>
      <p class="text-xs text-slate-500 mb-4">Rules scoped to <code class="cell-code">{{ data.exp.pattern_slug }}</code>, events since experiment was created.</p>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div class="metric-card">
          <h3 class="metric-card-title">Baseline <span class="metric-card-meta">no experiment</span></h3>
          <dl class="metric-list">
            <dt>Sessions</dt><dd>{{ data.baseline.sessions }}</dd>
            <dt>Checks</dt><dd>{{ data.baseline.checks }}</dd>
            <dt>Fired</dt>
            <dd>
              <Badge v-if="data.baseline.fired > 0" color="red" :label="String(data.baseline.fired)" />
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </dd>
            <dt>Fire rate</dt><dd class="font-mono">{{ fmtRate(data.baseline.rate) }}</dd>
          </dl>
        </div>
        <div class="metric-card">
          <h3 class="metric-card-title">Experiment <span class="metric-card-meta">concealed</span></h3>
          <dl class="metric-list">
            <dt>Sessions</dt><dd>{{ data.experiment.sessions }}</dd>
            <dt>Checks</dt><dd>{{ data.experiment.checks }}</dd>
            <dt>Fired</dt>
            <dd>
              <Badge v-if="data.experiment.fired > 0" color="red" :label="String(data.experiment.fired)" />
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </dd>
            <dt>Fire rate</dt><dd class="font-mono">{{ fmtRate(data.experiment.rate) }}</dd>
          </dl>
        </div>
      </div>
    </Card>

    <Card v-if="data.per_rule?.length" :no-padding="true">
      <div class="card-group-header">
        <h2 class="card-group-title">Per-rule breakdown</h2>
      </div>
      <table class="tbl">
        <thead>
          <tr>
            <th>Rule</th>
            <th class="text-right">Base checks</th>
            <th class="text-right">Base fired</th>
            <th class="text-right">Exp checks</th>
            <th class="text-right">Exp fired</th>
            <th class="text-right">&Delta; fire rate</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in data.per_rule" :key="r.rule_id">
            <td>
              <router-link :to="`/rules/${r.rule_id}`"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                <code class="cell-code">{{ r.rule_id }}</code>
              </router-link>
            </td>
            <td class="text-right font-mono text-xs">{{ r.baseline_checks }}</td>
            <td class="text-right">
              <Badge v-if="r.baseline_fired > 0" color="red" :label="String(r.baseline_fired)" />
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </td>
            <td class="text-right font-mono text-xs">{{ r.experiment_checks }}</td>
            <td class="text-right">
              <Badge v-if="r.experiment_fired > 0" color="red" :label="String(r.experiment_fired)" />
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </td>
            <td class="text-right">
              <Badge v-if="deltaColor(ruleRate(r.baseline_checks, r.baseline_fired), ruleRate(r.experiment_checks, r.experiment_fired))"
                :color="deltaColor(ruleRate(r.baseline_checks, r.baseline_fired), ruleRate(r.experiment_checks, r.experiment_fired))"
                :label="deltaLabel(ruleRate(r.baseline_checks, r.baseline_fired), ruleRate(r.experiment_checks, r.experiment_fired))" />
              <span v-else class="text-slate-500 text-xs font-mono">{{ deltaLabel(ruleRate(r.baseline_checks, r.baseline_fired), ruleRate(r.experiment_checks, r.experiment_fired)) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <Card>
      <details :open="editing">
        <summary class="edit-toggle focus-visible:outline-2 focus-visible:outline-blue-500"
          @click.prevent="editing = !editing">
          {{ editing ? 'Hide editor' : 'Edit experiment' }}
        </summary>
        <div v-if="editing" class="mt-4">
          <p class="text-xs text-slate-500 mb-4">If active, the deployed skill is redeployed immediately.</p>
          <div class="mb-3 max-w-sm">
            <label class="field-label">Name</label>
            <input type="text" v-model="editName"
              aria-label="Experiment name"
              :class="['input focus-visible:outline-2 focus-visible:outline-blue-500', editErrors.name ? 'is-invalid' : '']">
            <p v-if="editErrors.name" class="field-error">{{ editErrors.name }}</p>
          </div>
          <div class="mb-3">
            <label class="field-label">Sections to conceal</label>
            <div v-if="data.available_sections?.length" class="flex flex-wrap gap-x-4 gap-y-1 mt-1">
              <label v-for="s in data.available_sections" :key="s" class="check-chip">
                <input type="checkbox" :value="s" v-model="editSections" :aria-label="s">
                <code class="cell-code">{{ s }}</code>
              </label>
            </div>
            <span v-else class="text-slate-400 text-sm">No headings available.</span>
            <p v-if="editErrors.sections" class="field-error">{{ editErrors.sections }}</p>
          </div>
          <button type="button" class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
            @click="saveEdit">Save changes</button>
        </div>
      </details>
    </Card>
  </div>
</template>

<style scoped>
.metric-card {
    background: #F8FAFC;
    border: 1px solid #F1F5F9;
    border-radius: 0.75rem;
    padding: 1rem 1.125rem;
}
.metric-card-title {
    font-size: 0.8125rem;
    font-weight: 600;
    margin: 0 0 0.75rem;
    color: #0F172A;
}
.metric-card-meta {
    color: #94A3B8;
    font-weight: 400;
    margin-left: 0.25rem;
}
.metric-list {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.25rem 1rem;
    font-size: 0.8125rem;
}
.metric-list dt { color: #64748B; }
.metric-list dd { color: #0F172A; font-weight: 500; }

.edit-toggle {
    display: inline-flex;
    align-items: center;
    padding: 0.4375rem 0.875rem;
    font-size: 0.8125rem;
    font-weight: 500;
    background: #F1F5F9;
    color: #475569;
    border-radius: 0.625rem;
    cursor: pointer;
    list-style: none;
    border: 0;
}
.edit-toggle:hover { background: #E2E8F0; color: #0F172A; }
.edit-toggle::marker, .edit-toggle::-webkit-details-marker { display: none; }

.input.is-invalid {
    border-color: #F87171;
    box-shadow: 0 0 0 3px rgba(248, 113, 113, 0.15);
}
.field-error {
    font-size: 0.6875rem;
    color: #DC2626;
    margin-top: 0.25rem;
}

.check-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.8125rem;
    cursor: pointer;
}
.check-chip input { accent-color: #1E40AF; }
</style>
