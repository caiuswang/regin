<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()

const templates = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

const editing = ref(null)
const draft = ref(newDraft())

const PROVIDER_OPTIONS = [
  { id: 'external-agent', label: 'External Agent' },
  { id: 'langchain', label: 'LangChain' },
]

const sortedTemplates = computed(() =>
  [...templates.value].sort((a, b) => {
    if (a.builtin && !b.builtin) return -1
    if (!a.builtin && b.builtin) return 1
    return (a.label || '').localeCompare(b.label || '')
  }),
)

function newDraft() {
  return {
    slug: '',
    label: '',
    description: '',
    body: '',
    applies_to: [],
    default_for_providers: [],
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await api.get('/prompt-templates')
    templates.value = result.templates || []
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

function startNew() {
  editing.value = '__new__'
  draft.value = newDraft()
}

function startEdit(template) {
  editing.value = template.slug
  draft.value = {
    slug: template.slug,
    label: template.label || '',
    description: template.description || '',
    body: template.body || '',
    applies_to: [...(template.applies_to || [])],
    default_for_providers: [...(template.default_for_providers || [])],
  }
}

function cancelEdit() {
  editing.value = null
  draft.value = newDraft()
}

function toggleAppliesTo(providerId) {
  const set = new Set(draft.value.applies_to)
  if (set.has(providerId)) {
    set.delete(providerId)
    // Defaults are constrained to applies_to.
    draft.value.default_for_providers = draft.value.default_for_providers.filter(p => p !== providerId)
  } else {
    set.add(providerId)
  }
  draft.value.applies_to = Array.from(set)
}

function toggleDefault(providerId) {
  const set = new Set(draft.value.default_for_providers)
  if (set.has(providerId)) set.delete(providerId)
  else {
    set.add(providerId)
    // Ensure applies_to covers any default.
    if (!draft.value.applies_to.includes(providerId)) {
      draft.value.applies_to = [...draft.value.applies_to, providerId]
    }
  }
  draft.value.default_for_providers = Array.from(set)
}

async function saveDraft() {
  error.value = ''
  if (!draft.value.label.trim()) {
    error.value = 'Label is required.'
    return
  }
  if (!draft.value.body.trim()) {
    error.value = 'Body is required.'
    return
  }
  busy.value = 'save'
  try {
    const payload = {
      label: draft.value.label.trim(),
      description: draft.value.description.trim(),
      body: draft.value.body,
      applies_to: draft.value.applies_to,
      default_for_providers: draft.value.default_for_providers,
    }
    let result
    if (editing.value === '__new__') {
      payload.slug = draft.value.slug.trim()
      result = await api.post('/prompt-templates', payload)
    } else {
      result = await api.patch(`/prompt-templates/${editing.value}`, payload)
    }
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

async function deleteTemplate(template) {
  const ok = await confirm('Delete prompt template', `Delete "${template.label}"? This cannot be undone.`, true)
  if (!ok) return
  busy.value = `delete-${template.slug}`
  try {
    const result = await api.delete(`/prompt-templates/${template.slug}`)
    if (!result.ok) {
      error.value = result.error || 'Delete failed'
      return
    }
    await load()
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-if="loading" class="empty-state">Loading prompt templates…</div>
  <div v-else>
    <div class="panel-toolbar">
      <p class="panel-intro">
        Reusable prompt fragments injected into topic-proposal LLM/agent flows.
        Toggle <em>default for provider</em> to auto-select a template when that provider is picked.
      </p>
      <Button variant="primary" :disabled="editing === '__new__'" @click="startNew">
        New template
      </Button>
    </div>

    <div v-if="error" class="alert alert-info">{{ error }}</div>

    <Card v-if="editing" class="prompt-template-editor">
      <h2 class="text-lg font-semibold mb-3">
        {{ editing === '__new__' ? 'New template' : `Edit “${draft.label}”` }}
      </h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label v-if="editing === '__new__'" class="block">
          <span class="form-label">Slug (optional)</span>
          <input v-model="draft.slug" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" placeholder="auto-generated from label">
        </label>
        <label class="block">
          <span class="form-label">Label *</span>
          <input v-model="draft.label" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" required>
        </label>
        <label class="block md:col-span-2">
          <span class="form-label">Description</span>
          <input v-model="draft.description" class="topics-input w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" placeholder="One-line summary shown on the chip tooltip">
        </label>
        <div class="block md:col-span-2">
          <span class="form-label">Applies to providers</span>
          <div class="flex flex-wrap gap-2 mt-1">
            <button
              v-for="opt in PROVIDER_OPTIONS"
              :key="opt.id"
              type="button"
              class="topics-template-chip focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              :class="{ 'topics-template-chip-active': draft.applies_to.includes(opt.id) }"
              @click="toggleAppliesTo(opt.id)"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="block md:col-span-2">
          <span class="form-label">Default-on for providers</span>
          <div class="flex flex-wrap gap-2 mt-1">
            <button
              v-for="opt in PROVIDER_OPTIONS"
              :key="opt.id"
              type="button"
              class="topics-template-chip focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              :class="{ 'topics-template-chip-active': draft.default_for_providers.includes(opt.id) }"
              @click="toggleDefault(opt.id)"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <label class="block md:col-span-2">
          <span class="form-label">Body *</span>
          <textarea v-model="draft.body" rows="10" class="topics-input w-full font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"></textarea>
        </label>
      </div>
      <div class="mt-4 flex gap-2 justify-end">
        <Button variant="secondary" :disabled="busy === 'save'" @click="cancelEdit">Cancel</Button>
        <Button variant="primary" :disabled="busy === 'save'" @click="saveDraft">
          {{ busy === 'save' ? 'Saving…' : 'Save' }}
        </Button>
      </div>
    </Card>

    <Card :no-padding="true">
      <table class="tbl">
        <thead>
          <tr>
            <th>Template</th>
            <th>Applies to</th>
            <th>Default for</th>
            <th class="text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in sortedTemplates" :key="t.slug">
            <td>
              <div class="flex items-center gap-2">
                <span class="font-medium">{{ t.label }}</span>
                <Badge v-if="t.builtin" color="purple" label="built-in" />
              </div>
              <div class="text-xs text-slate-500"><code>{{ t.slug }}</code></div>
              <div v-if="t.description" class="text-xs text-slate-600 mt-1">{{ t.description }}</div>
            </td>
            <td>
              <span v-if="!(t.applies_to || []).length" class="text-slate-400">all</span>
              <span v-else class="flex flex-wrap gap-1">
                <Badge v-for="p in t.applies_to" :key="p" color="gray" :label="p" />
              </span>
            </td>
            <td>
              <span v-if="!(t.default_for_providers || []).length" class="text-slate-400">—</span>
              <span v-else class="flex flex-wrap gap-1">
                <Badge v-for="p in t.default_for_providers" :key="p" color="blue" :label="p" />
              </span>
            </td>
            <td class="text-right">
              <Button
                variant="secondary"
                size="sm"
                class="mr-1"
                @click="startEdit(t)"
              >
                Edit
              </Button>
              <Button
                variant="secondary"
                size="sm"
                :disabled="t.builtin || busy === `delete-${t.slug}`"
                :title="t.builtin ? 'Built-in templates cannot be deleted' : ''"
                @click="deleteTemplate(t)"
              >
                Delete
              </Button>
            </td>
          </tr>
          <tr v-if="!sortedTemplates.length">
            <td colspan="4" class="empty-row">No prompt templates yet. Click <em>New template</em> to create one.</td>
          </tr>
        </tbody>
      </table>
    </Card>
  </div>
</template>

<style scoped>
.panel-toolbar {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
}
.panel-intro {
    font-size: 0.85rem;
    color: var(--color-slate-600);
    max-width: 48rem;
}
.empty-row {
    color: var(--color-slate-400);
    text-align: center;
    padding: 1.5rem;
    font-size: 0.875rem;
}
.form-label {
    display: block;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-gray-500);
    margin-bottom: 0.25rem;
}
.prompt-template-editor {
    margin-bottom: 1.25rem;
}
</style>
