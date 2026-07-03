<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import PromptSkeletonEditor from './PromptSkeletonEditor.vue'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()

const skeletons = ref([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

const editingSlug = ref(null)
const saveError = ref('')

// Literal double-brace strings can't be written inline in a Vue template
// (the tokenizer reads the inner }} as an interpolation close), so build it here.
const VAR_SYNTAX = '{{variable}}'

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
  // Toggle the inline editor open/closed under the clicked row.
  editingSlug.value = editingSlug.value === skeleton.slug ? null : skeleton.slug
  saveError.value = ''
  error.value = ''
}

function cancelEdit() {
  editingSlug.value = null
  saveError.value = ''
}

async function onSave(body) {
  saveError.value = ''
  busy.value = 'save'
  try {
    const result = await api.patch(`/prompt-templates/${editingSlug.value}`, { body })
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
    // load() replaces the skeleton object; the open editor watches skeleton.body
    // and picks up the restored default.
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
  <div v-if="loading" class="empty-state">Loading prompt skeletons…</div>
  <div v-else>
    <p class="panel-intro">
      The system/goal prompts regin pipes to external agents. Edit the body inline;
      <code>{{ VAR_SYNTAX }}</code> slots are filled at run time from the palette below.
      A broken edit safely falls back to the built-in default. Use <em>Reset</em> to restore it.
    </p>

    <div v-if="error" class="alert alert-info">{{ error }}</div>

    <div v-for="group in grouped" :key="group.area" class="area-group">
      <h3 class="area-title">{{ group.label }}</h3>
      <Card :no-padding="true">
        <table class="tbl">
          <tbody>
            <template v-for="s in group.items" :key="s.slug">
              <tr :class="{ 'row-editing': editingSlug === s.slug }">
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
                  <Button variant="secondary" size="sm" class="mr-1" @click="startEdit(s)">
                    {{ editingSlug === s.slug ? 'Close' : 'Edit' }}
                  </Button>
                  <Button variant="secondary" size="sm" :disabled="busy === 'reset'" @click="resetToDefault(s)">
                    Reset
                  </Button>
                </td>
              </tr>
              <tr v-if="editingSlug === s.slug" class="editor-row">
                <td colspan="2">
                  <PromptSkeletonEditor
                    :skeleton="s"
                    :busy="busy"
                    :save-error="saveError"
                    @save="onSave"
                    @cancel="cancelEdit"
                    @reset="resetToDefault(s)"
                  />
                </td>
              </tr>
            </template>
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
.area-group { margin-bottom: 1.25rem; }
.area-title {
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-slate-500);
    margin: 0 0 0.4rem;
}
.row-editing > td {
    border-bottom: none;
}
.editor-row > td {
    padding: 0 0.75rem 0.75rem;
    background: var(--color-slate-50);
}
</style>
