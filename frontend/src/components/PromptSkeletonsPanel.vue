<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import PromptSkeletonEditor from './PromptSkeletonEditor.vue'
import SurfaceAgentPicker from './SurfaceAgentPicker.vue'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()

const skeletons = ref([])
const agents = ref([])
const defaultAgent = ref(null)
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
    const [templates, agentList] = await Promise.all([
      api.get('/prompt-templates?kind=skeleton'),
      api.get('/agents'),
    ])
    skeletons.value = templates.templates || []
    agents.value = agentList.agents || []
    defaultAgent.value = agentList.default || null
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function onBindAgent(skeleton, agentId) {
  error.value = ''
  busy.value = 'bind'
  try {
    const result = await api.patch(`/prompt-templates/${skeleton.slug}`, { agent: agentId })
    if (!result.ok) {
      error.value = result.error || 'Could not bind agent'
      return
    }
    skeleton.agent = result.template?.agent ?? agentId
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    busy.value = ''
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

    <Card class="agents-card">
      <div class="agents-card-head">
        <h3 class="area-title">Agents</h3>
        <span v-if="agents.length" class="agents-count">{{ agents.length }} configured</span>
      </div>
      <p v-if="!agents.length" class="agents-empty">
        No external agents configured. Add one under
        <code>topic_proposal_external_agents</code> in <code>settings.local.json</code>,
        then bind a goal to it here.
      </p>
      <ul v-else class="agents-list">
        <li v-for="a in agents" :key="a.id">
          <span class="agent-id">{{ a.id }}</span>
          <Badge v-if="a.id === defaultAgent" color="gray" label="default" />
          <code class="agent-cmd">{{ a.command }}</code>
        </li>
      </ul>
    </Card>

    <div v-for="group in grouped" :key="group.area" class="area-group">
      <h3 class="area-title">{{ group.label }}</h3>
      <Card :no-padding="true">
        <table class="tbl">
          <tbody>
            <template v-for="s in group.items" :key="s.slug">
              <tr :class="{ 'row-editing': editingSlug === s.slug, 'tbl-row-active': editingSlug === s.slug }">
                <td class="prompt-cell">
                  <div class="flex items-center gap-2">
                    <span class="font-medium">{{ s.label }}</span>
                    <Badge v-if="!s.builtin" color="blue" label="custom" />
                  </div>
                  <div v-if="s.description" class="row-desc">{{ s.description }}</div>
                  <div class="row-meta">
                    <code class="row-slug">{{ s.slug }}</code>
                    <span class="row-dot">·</span>
                    <span>{{ (s.variables || []).length }} variable(s)</span>
                  </div>
                </td>
                <td class="controls-cell">
                  <div class="controls-cluster">
                    <SurfaceAgentPicker
                      :model-value="s.agent"
                      :agents="agents"
                      :default-agent="defaultAgent"
                      :disabled="busy === 'bind'"
                      @update:model-value="(v) => onBindAgent(s, v)"
                    />
                    <div class="action-btns">
                      <Button variant="secondary" size="sm" @click="startEdit(s)">
                        {{ editingSlug === s.slug ? 'Close' : 'Edit' }}
                      </Button>
                      <Button variant="secondary" size="sm" :disabled="busy === 'reset'" @click="resetToDefault(s)">
                        Reset
                      </Button>
                    </div>
                  </div>
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
    margin: 0;
}
.agents-card { margin-bottom: 1.5rem; }
.agents-card-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.6rem;
}
.agents-count {
    font-size: 0.72rem;
    color: var(--color-slate-400);
    white-space: nowrap;
}
.agents-empty {
    font-size: 0.8rem;
    color: var(--color-slate-500);
    margin: 0;
}
.agents-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    gap: 0.1rem 1.25rem;
}
.agents-list li {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.25rem 0;
    min-width: 0;
}
.agent-id { font-weight: 500; font-size: 0.82rem; }
.agent-cmd {
    color: var(--color-slate-500);
    font-size: 0.75rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.prompt-cell { width: 100%; }
.controls-cell {
    white-space: nowrap;
    vertical-align: top;
    text-align: right;
}
.controls-cluster {
    display: inline-flex;
    align-items: center;
    gap: 0.75rem;
}
.controls-cluster :deep(.ds-select-wrap) { min-width: 9.5rem; }
.action-btns {
    display: inline-flex;
    gap: 0.25rem;
}
.row-desc {
    font-size: 0.75rem;
    color: var(--color-slate-600);
    margin-top: 0.2rem;
    max-width: 46rem;
}
.row-meta {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-top: 0.3rem;
    font-size: 0.72rem;
    color: var(--color-slate-400);
}
.row-slug { font-family: var(--font-mono, monospace); }
.row-dot { color: var(--color-slate-300); }
</style>
