<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import PromptFragmentEditor from './PromptFragmentEditor.vue'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()

const templates = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
const saveError = ref('')

// slug of the row being edited, '__new__' while creating, or null.
const editing = ref(null)

const sortedTemplates = computed(() =>
  [...templates.value].sort((a, b) => {
    if (a.builtin && !b.builtin) return -1
    if (!a.builtin && b.builtin) return 1
    return (a.label || '').localeCompare(b.label || '')
  }),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await api.get('/prompt-templates?kind=fragment')
    templates.value = result.templates || []
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

function startNew() {
  editing.value = '__new__'
  saveError.value = ''
  error.value = ''
}

function startEdit(template) {
  // Toggle the inline editor open/closed under the clicked row.
  editing.value = editing.value === template.slug ? null : template.slug
  saveError.value = ''
  error.value = ''
}

function cancelEdit() {
  editing.value = null
  saveError.value = ''
}

async function onSave(payload) {
  saveError.value = ''
  busy.value = 'save'
  try {
    const result = editing.value === '__new__'
      ? await api.post('/prompt-templates', payload)
      : await api.patch(`/prompt-templates/${editing.value}`, payload)
    if (!result.ok) {
      saveError.value = result.error || 'Save failed'
      return
    }
    await load()
    cancelEdit()
  } catch (err) {
    saveError.value = err.message || String(err)
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

    <Card :no-padding="true">
      <div class="overflow-x-auto">
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
          <tr v-if="editing === '__new__'" class="editor-row">
            <td colspan="4">
              <PromptFragmentEditor
                :template="null"
                :busy="busy"
                :save-error="saveError"
                @save="onSave"
                @cancel="cancelEdit"
              />
            </td>
          </tr>
          <template v-for="t in sortedTemplates" :key="t.slug">
            <tr :class="{ 'row-editing': editing === t.slug, 'tbl-row-active': editing === t.slug }">
              <td>
                <div class="flex items-center gap-2">
                  <span class="font-medium">{{ t.label }}</span>
                  <Badge v-if="!t.builtin" color="blue" label="custom" />
                </div>
                <div class="row-meta"><code class="row-slug">{{ t.slug }}</code></div>
                <div v-if="t.description" class="row-desc">{{ t.description }}</div>
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
              <td class="text-right actions-cell">
                <Button variant="secondary" size="sm" class="mr-1" @click="startEdit(t)">
                  {{ editing === t.slug ? 'Close' : 'Edit' }}
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
            <tr v-if="editing === t.slug" class="editor-row">
              <td colspan="4">
                <PromptFragmentEditor
                  :template="t"
                  :busy="busy"
                  :save-error="saveError"
                  @save="onSave"
                  @cancel="cancelEdit"
                />
              </td>
            </tr>
          </template>
          <tr v-if="!sortedTemplates.length && editing !== '__new__'">
            <td colspan="4" class="empty-row">No prompt templates yet. Click <em>New template</em> to create one.</td>
          </tr>
        </tbody>
      </table>
      </div>
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
.actions-cell { white-space: nowrap; vertical-align: top; }
.row-meta {
    margin-top: 0.2rem;
    font-size: 0.72rem;
    color: var(--color-slate-400);
}
.row-slug { font-family: var(--font-mono, monospace); }
.row-desc {
    font-size: 0.75rem;
    color: var(--color-slate-600);
    margin-top: 0.25rem;
    max-width: 46rem;
}
</style>
