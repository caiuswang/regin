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

const editingMeta = ref(false)
const editSummary = ref('')
const editSeverity = ref('')
const editTriggers = ref('')
const editLayer = ref('')
const editGuide = ref('')

const editingSource = ref(false)
const editSourceCode = ref('')
const savingSource = ref(false)

function engineLabel() {
  return data.value?.engine?.kind || data.value?.rule?.engine_kind || data.value?.rule?.engine || 'rule'
}

function canEditMeta()   { return !!data.value?.rule?.capabilities?.can_edit_metadata }
function canEditSource() { return !!data.value?.rule?.capabilities?.can_edit_source }
function canDeleteRule() { return !!data.value?.rule?.capabilities?.can_delete }
function canShowSource() { return !!data.value?.rule?.capabilities?.can_show_source }
function canTestRun()    { return !!data.value?.rule?.capabilities?.can_test_run }

async function load() {
  data.value = await api.get(`/rules/${route.params.id}`)
  loading.value = false
}

onMounted(load)

function startEditingMeta() {
  editSummary.value = data.value.rule.summary || ''
  editSeverity.value = data.value.rule.severity || ''
  editTriggers.value = (data.value.rule.triggers || []).join(', ')
  editLayer.value = data.value.rule.layer || ''
  editGuide.value = data.value.rule.guide || ''
  editingMeta.value = true
}

async function saveMeta() {
  const result = await api.post(`/rules/${route.params.id}/update`, {
    summary: editSummary.value,
    severity: editSeverity.value,
    triggers: editTriggers.value,
    layer: editLayer.value,
    guide: editGuide.value,
  })
  if (!result.ok) { flash(result.msg || 'Failed to save', 'error'); return }
  flash(result.msg)
  editingMeta.value = false
  await load()
}

function startEditingSource() {
  editSourceCode.value = data.value.source_snippet || ''
  editingSource.value = true
}

async function saveSource() {
  savingSource.value = true
  const result = await api.post(`/rules/${route.params.id}/update`, { source: editSourceCode.value })
  savingSource.value = false
  if (!result.ok) { flash(result.msg || 'Failed to save', 'error'); return }
  flash(result.msg)
  editingSource.value = false
  await load()
}

async function deleteRule() {
  const ok = await confirm('Delete rule', `Permanently delete rule "${route.params.id}"? This removes it from the .grit source file.`, true)
  if (!ok) return
  const result = await api.post(`/rules/${route.params.id}/delete`)
  if (!result.ok) { flash(result.msg || 'Failed to delete', 'error'); return }
  flash(result.msg)
  router.push('/rules')
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading rule…</div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Rules', to: '/rules' },
      { label: data.rule.layer, to: `/rules?by=layer` },
      { label: data.rule.id },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Rule · {{ data.rule.layer }}</div>
        <h1 class="page-title"><code class="cell-code text-base">{{ data.rule.id }}</code></h1>
        <p class="page-subtitle">{{ data.rule.summary }}</p>
      </div>
    </header>

    <Card>
      <div class="card-header-row">
        <h2 class="card-header">Metadata</h2>
        <div v-if="!editingMeta && canEditMeta()">
          <button type="button" @click="startEditingMeta"
            class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">Edit</button>
        </div>
        <div v-else-if="editingMeta" class="flex gap-2">
          <button type="button" @click="saveMeta"
            class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500">Save</button>
          <button type="button" @click="editingMeta = false"
            class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">Cancel</button>
        </div>
      </div>

      <dl v-if="!editingMeta" class="meta-list">
        <dt>Layer</dt><dd><code class="cell-code">{{ data.rule.layer }}</code></dd>
        <dt>Engine</dt>
        <dd>
          <Badge color="gray" :label="engineLabel()" />
          <code class="cell-code ml-2">{{ data.rule.engine }}</code>
        </dd>
        <dt>Severity</dt>
        <dd>
          <Badge v-if="data.rule.severity === 'error'" color="red" :label="data.rule.severity" />
          <Badge v-else-if="data.rule.severity === 'warn'" color="yellow" :label="data.rule.severity" />
          <span v-else class="text-slate-500">{{ data.rule.severity }}</span>
        </dd>
        <dt>Triggers</dt>
        <dd>
          <code v-for="t in data.rule.triggers" :key="t" class="cell-code mr-1">{{ t }}</code>
        </dd>
        <dt>Pattern guide</dt>
        <dd>
          <router-link v-if="data.rule.guide_kind === 'pattern'" :to="`/patterns/${data.rule.guide}`"
            class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ data.rule.guide }}
          </router-link>
          <router-link v-else-if="data.rule.guide_kind === 'auto'" :to="`/skills/${data.rule.guide}`"
            class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ data.rule.guide }}
          </router-link>
          <span v-else class="text-slate-500">{{ data.rule.guide }}</span>
        </dd>
        <dt>Source file</dt><dd><code class="cell-code">{{ data.rule.source_file }}</code></dd>
      </dl>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl">
        <div>
          <label class="field-label">Summary</label>
          <input type="text" v-model="editSummary" aria-label="Summary"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div>
          <label class="field-label">Severity</label>
          <select v-model="editSeverity" aria-label="Severity"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
            <option value="error">error</option>
            <option value="warn">warn</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
        </div>
        <div>
          <label class="field-label">Layer</label>
          <input type="text" v-model="editLayer" aria-label="Layer"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div>
          <label class="field-label">Guide (procedure ID)</label>
          <input type="text" v-model="editGuide" aria-label="Guide procedure ID"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
        <div class="col-span-2">
          <label class="field-label">Triggers (comma-separated)</label>
          <input type="text" v-model="editTriggers" aria-label="Triggers (comma-separated)"
            class="input focus-visible:outline-2 focus-visible:outline-blue-500">
        </div>
      </div>
    </Card>

    <Card v-if="canShowSource() && (data.source_snippet || editingSource)">
      <div class="card-header-row">
        <h2 class="card-header">{{ data.ui?.source_label || 'Rule source' }}</h2>
        <div v-if="!editingSource && canEditSource()">
          <button type="button" @click="startEditingSource"
            class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">Edit</button>
        </div>
        <div v-else-if="editingSource" class="flex gap-2">
          <button type="button" @click="saveSource" :disabled="savingSource"
            class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ savingSource ? 'Saving…' : 'Save' }}
          </button>
          <button type="button" @click="editingSource = false"
            class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">Cancel</button>
        </div>
      </div>
      <div v-if="editingSource">
        <textarea v-model="editSourceCode" rows="20" aria-label="Rule source code"
          class="input font-mono code-textarea focus-visible:outline-2 focus-visible:outline-blue-500"
          spellcheck="false"></textarea>
        <p class="text-xs text-slate-400 mt-1">{{ data.ui?.source_help }}</p>
      </div>
      <pre v-else class="code-block"><code>{{ data.source_snippet }}</code></pre>
    </Card>

    <Card>
      <h2 class="card-header">How it runs</h2>
      <p class="text-sm text-slate-600 mb-3">
        <strong>Automatic:</strong> {{ data.ui?.automatic_run_description }}
      </p>
      <template v-if="canTestRun() && data.engine?.invocation_hint">
        <p class="text-sm text-slate-600 mb-2"><strong>Run this check:</strong></p>
        <pre class="code-block"><code>cd &lt;repo-path&gt;
{{ data.engine.invocation_hint.replace('&lt;rule-id&gt;', data.rule.id) }}</code></pre>
      </template>
    </Card>

    <Card v-if="canDeleteRule()">
      <h2 class="card-header">Danger zone</h2>
      <p class="text-xs text-slate-500 mb-3">Permanently remove this rule from its source file.</p>
      <button type="button" @click="deleteRule"
        class="btn btn-danger focus-visible:outline-2 focus-visible:outline-blue-500">
        Delete rule
      </button>
    </Card>
  </div>
</template>

<style scoped>
.code-textarea {
    background: #F8FAFC;
    font-size: 0.8125rem;
    line-height: 1.5;
    min-height: 24rem;
}
</style>
