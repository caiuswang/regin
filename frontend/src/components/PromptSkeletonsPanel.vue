<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import { useConfirm } from '../composables/useConfirm'
import { renderPreview, unknownVariables } from '../composables/usePromptPreview'

const { confirm } = useConfirm()

const skeletons = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

const editingSlug = ref(null)
const draftBody = ref('')
const bodyRef = ref(null)

// Literal double-brace strings can't be written inline in a Vue template
// (the tokenizer reads the inner }} as an interpolation close), so build them here.
const VAR_SYNTAX = '{{variable}}'
function varToken(name) {
  return `{{${name}}}`
}

const AREA_LABELS = {
  'topic-proposal': 'Topic proposal',
  'topic-graph': 'Topic graph',
  memory: 'Memory',
  grader: 'Grader',
}

function areaOf(slug) {
  if (slug.startsWith('topic-proposal')) return 'topic-proposal'
  if (slug.startsWith('grader')) return 'grader'
  if (slug.startsWith('memory')) return 'memory'
  if (slug.startsWith('topic')) return 'topic-graph'
  return 'other'
}

const grouped = computed(() => {
  const buckets = new Map()
  for (const s of skeletons.value) {
    const area = areaOf(s.slug)
    if (!buckets.has(area)) buckets.set(area, [])
    buckets.get(area).push(s)
  }
  return [...buckets.entries()]
    .map(([area, items]) => ({
      area,
      label: AREA_LABELS[area] || 'Other',
      items: items.sort((a, b) => (a.label || '').localeCompare(b.label || '')),
    }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

const current = computed(() => skeletons.value.find(s => s.slug === editingSlug.value) || null)
const currentVars = computed(() => current.value?.variables || [])

const unknownVars = computed(() =>
  current.value ? unknownVariables(draftBody.value, currentVars.value) : [],
)
const preview = computed(() =>
  current.value ? renderPreview(draftBody.value, currentVars.value) : '',
)
const unknownVarsLabel = computed(() => unknownVars.value.map(varToken).join(', '))
const dirty = computed(() => current.value && draftBody.value !== (current.value.body || ''))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await api.get('/prompt-templates?kind=skeleton')
    skeletons.value = result.templates || []
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

function startEdit(skeleton) {
  editingSlug.value = skeleton.slug
  draftBody.value = skeleton.body || ''
  error.value = ''
}

function cancelEdit() {
  editingSlug.value = null
  draftBody.value = ''
}

function insertVar(name) {
  const token = `{{${name}}}`
  const el = bodyRef.value
  if (!el) {
    draftBody.value += token
    return
  }
  // Insert at the caret (replacing any selection) instead of appending.
  const start = el.selectionStart ?? draftBody.value.length
  const end = el.selectionEnd ?? start
  const text = draftBody.value
  draftBody.value = text.slice(0, start) + token + text.slice(end)
  nextTick(() => {
    el.focus()
    const caret = start + token.length
    el.setSelectionRange(caret, caret)
  })
}

async function saveDraft() {
  error.value = ''
  if (!draftBody.value.trim()) {
    error.value = 'Body cannot be empty.'
    return
  }
  if (unknownVars.value.length) {
    error.value = `Unknown variable(s): ${unknownVarsLabel.value}. `
      + 'Only the declared variables above are filled at render time — remove these or a run falls back to the default.'
    return
  }
  busy.value = 'save'
  try {
    const result = await api.patch(`/prompt-templates/${editingSlug.value}`, { body: draftBody.value })
    if (!result.ok) {
      error.value = result.error || 'Save failed'
      return
    }
    await load()
    cancelEdit()
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    busy.value = ''
  }
}

async function resetToDefault(skeleton) {
  const ok = await confirm(
    'Reset to default',
    `Restore “${skeleton.label}” to its built-in default prompt? Your edits will be lost.`,
    true,
  )
  if (!ok) return
  busy.value = 'reset'
  try {
    const result = await api.post(`/prompt-templates/${skeleton.slug}/reset`, {})
    if (!result.ok) {
      error.value = result.error || 'Reset failed'
      return
    }
    await load()
    if (editingSlug.value === skeleton.slug) draftBody.value = result.template.body || ''
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="empty-state">Loading prompt skeletons…</div>
  <div v-else>
    <p class="panel-intro">
      The system/goal prompts regin pipes to external agents. Edit the body inline;
      <code>{{ VAR_SYNTAX }}</code> slots are filled at run time from the palette below.
      A broken edit safely falls back to the built-in default. Use <em>Reset</em> to restore it.
    </p>

    <div v-if="error" class="alert alert-info">{{ error }}</div>

    <Card v-if="current" class="skeleton-editor">
      <div class="editor-head">
        <div>
          <h2 class="text-lg font-semibold">{{ current.label }}</h2>
          <code class="text-xs text-slate-500">{{ current.slug }}</code>
        </div>
        <div class="flex gap-2">
          <Button variant="secondary" size="sm" :disabled="busy === 'reset'" @click="resetToDefault(current)">
            Reset to default
          </Button>
        </div>
      </div>

      <div v-if="currentVars.length" class="var-palette">
        <span class="palette-label">Variables</span>
        <Button
          v-for="v in currentVars"
          :key="v.name"
          variant="ghost"
          size="sm"
          class="var-chip"
          :title="v.description || v.name"
          @click="insertVar(v.name)"
        >
          {{ varToken(v.name) }}
          <span v-if="v.required === false" class="var-optional">opt</span>
        </Button>
      </div>

      <div class="editor-grid">
        <label class="block">
          <span class="form-label">Body</span>
          <textarea
            ref="bodyRef"
            v-model="draftBody"
            rows="18"
            class="topics-input w-full font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          ></textarea>
        </label>
        <div class="block">
          <span class="form-label">Live preview (sample values)</span>
          <pre class="preview-pane">{{ preview }}</pre>
        </div>
      </div>

      <div v-if="unknownVars.length" class="alert alert-warn mt-2">
        Unknown variable(s): {{ unknownVarsLabel }} — not filled at render time.
      </div>

      <div class="mt-4 flex gap-2 justify-end">
        <Button variant="secondary" :disabled="busy === 'save'" @click="cancelEdit">Close</Button>
        <Button variant="primary" :disabled="busy === 'save' || !dirty || unknownVars.length > 0" @click="saveDraft">
          {{ busy === 'save' ? 'Saving…' : 'Save' }}
        </Button>
      </div>
    </Card>

    <div v-for="group in grouped" :key="group.area" class="area-group">
      <h3 class="area-title">{{ group.label }}</h3>
      <Card :no-padding="true">
        <table class="tbl">
          <tbody>
            <tr v-for="s in group.items" :key="s.slug">
              <td>
                <div class="flex items-center gap-2">
                  <span class="font-medium">{{ s.label }}</span>
                  <Badge color="purple" label="skeleton" />
                  <Badge v-if="s.builtin" color="gray" label="built-in" />
                </div>
                <div v-if="s.description" class="text-xs text-slate-600 mt-1">{{ s.description }}</div>
                <div class="text-xs text-slate-400 mt-1">{{ (s.variables || []).length }} variable(s)</div>
              </td>
              <td class="text-right">
                <Button variant="secondary" size="sm" class="mr-1" @click="startEdit(s)">Edit</Button>
                <Button variant="secondary" size="sm" :disabled="busy === 'reset'" @click="resetToDefault(s)">
                  Reset
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>

    <div v-if="!skeletons.length" class="empty-state">
      No prompt skeletons registered. Run <code>regin init</code> or <code>regin rebuild</code> to seed them.
    </div>
  </div>
</template>

<style scoped>
.panel-intro {
    font-size: 0.85rem;
    color: var(--color-slate-600);
    max-width: 52rem;
    margin-bottom: 1rem;
}
.skeleton-editor {
    margin-bottom: 1.25rem;
}
.editor-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.75rem;
}
.var-palette {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.75rem;
}
.palette-label {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-gray-500);
}
.var-chip {
    font-family: var(--font-mono, monospace);
    border: 1px solid var(--color-slate-300);
}
.var-optional {
    color: var(--color-slate-400);
    margin-left: 0.25rem;
}
.editor-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
}
@media (max-width: 900px) {
    .editor-grid { grid-template-columns: 1fr; }
}
.preview-pane {
    background: var(--color-slate-50);
    border: 1px solid var(--color-slate-200);
    border-radius: 0.4rem;
    padding: 0.6rem;
    font-size: 0.72rem;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 27rem;
    overflow: auto;
}
.form-label {
    display: block;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-gray-500);
    margin-bottom: 0.25rem;
}
.area-group { margin-bottom: 1.25rem; }
.area-title {
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-slate-500);
    margin: 0 0 0.4rem;
}
</style>
