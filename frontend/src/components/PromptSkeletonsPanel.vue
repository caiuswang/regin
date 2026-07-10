<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Badge from './Badge.vue'
import Card from './Card.vue'
import Button from './ui/Button.vue'
import PromptSkeletonEditor from './PromptSkeletonEditor.vue'
import SurfaceAgentPicker from './SurfaceAgentPicker.vue'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()
const route = useRoute()

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

function areaLabelOf(s) {
  return AREA_LABELS[areaOf(s.slug)] || 'Other'
}

// Categorical color per area so same-category cards cohere at a glance in the
// flat grid (a color legend stands in for the section separators the uniform
// tiling can't have). Soft badge palette — grouping cue, not a loud accent.
const AREA_COLORS = {
  grader: 'purple',
  memory: 'blue',
  'topic-graph': 'yellow',
  'topic-proposal': 'green',
}

function areaColorOf(s) {
  return AREA_COLORS[areaOf(s.slug)] || 'gray'
}

// One flat, uniformly-tiled grid — NOT a separate grid per area. Grouping by
// area fragmented the grid so small groups (Grader: 2) left whole empty
// columns while large ones (Memory: 6) wrapped raggedly. Sorting by (area,
// label) keeps each category visually clustered — the slug prefix + the card
// eyebrow still name the area — while the grid stays dense at any width.
const sortedSkeletons = computed(() =>
  [...skeletons.value].sort(
    (a, b) =>
      areaLabelOf(a).localeCompare(areaLabelOf(b)) ||
      (a.label || '').localeCompare(b.label || ''),
  ),
)

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

async function onSave({ body, tags }) {
  saveError.value = ''
  busy.value = 'save'
  try {
    const result = await api.patch(`/prompt-templates/${editingSlug.value}`, { body, tags })
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

// Deep-link target: `/prompt-templates?surface=<slug>` opens that skeleton's
// editor and scrolls it into view (the pipeline's `configure →` link).
async function openFromRoute() {
  const want = route.query.surface
  if (!want || !skeletons.value.some((s) => s.slug === want)) return
  editingSlug.value = want
  await nextTick()
  document.getElementById(`sk-${want}`)?.scrollIntoView({ block: 'center' })
}

onMounted(async () => {
  await load()
  await openFromRoute()
})
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

    <div class="prompt-grid">
      <div
        v-for="s in sortedSkeletons"
        :id="`sk-${s.slug}`"
        :key="s.slug"
        class="prompt-card"
        :class="{ 'prompt-card-editing': editingSlug === s.slug }"
      >
        <div class="prompt-card-main">
          <div class="card-eyebrow">
            <Badge :color="areaColorOf(s)" :label="areaLabelOf(s)" />
            <Badge v-if="!s.builtin" color="gray" label="custom" />
          </div>
          <div class="font-medium mt-1">{{ s.label }}</div>
          <div v-if="s.description" class="row-desc">{{ s.description }}</div>
          <div class="row-meta">
            <code class="row-slug">{{ s.slug }}</code>
            <span class="row-dot">·</span>
            <span>{{ (s.variables || []).length }} variable(s)</span>
            <template v-if="(s.tags || []).length">
              <span class="row-dot">·</span>
              <span class="row-tags">
                <span v-for="t in s.tags" :key="t" class="row-tag">{{ t }}</span>
              </span>
            </template>
          </div>
        </div>
        <div class="prompt-card-controls">
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
        <PromptSkeletonEditor
          v-if="editingSlug === s.slug"
          :skeleton="s"
          :busy="busy"
          :save-error="saveError"
          @save="onSave"
          @cancel="cancelEdit"
          @reset="resetToDefault(s)"
        />
      </div>
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
    /* min(18rem, 100%) so the column shrinks below 18rem on a phone-width
       pane instead of forcing a hard 288px min that overflows sideways. */
    grid-template-columns: repeat(auto-fill, minmax(min(18rem, 100%), 1fr));
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
.prompt-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(28rem, 100%), 1fr));
    gap: 0.75rem;
    align-items: start;
}
.prompt-card {
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.875rem 1rem;
    transition: border-color 150ms, box-shadow 150ms;
}
.prompt-card:hover { border-color: var(--color-border-strong); }
/* An open editor takes the full grid width so the body + live-preview
   two-pane layout has room; the accent rail mirrors .tbl-row-active. */
.prompt-card-editing {
    grid-column: 1 / -1;
    border-color: var(--color-blue-500);
    box-shadow: inset 3px 0 0 var(--color-blue-500);
}
.prompt-card-editing:hover { border-color: var(--color-blue-500); }
.prompt-card-main { flex: 1 1 auto; min-width: 0; }
.card-eyebrow {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.1rem;
}
.prompt-card-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-top: 0.85rem;
}
.prompt-card-controls :deep(.ds-select-wrap) { min-width: 9.5rem; }
.prompt-card-editing :deep(.skeleton-editor) {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px solid var(--color-border);
}
.action-btns {
    display: inline-flex;
    gap: 0.25rem;
}
.row-desc {
    font-size: 0.75rem;
    color: var(--color-slate-600);
    margin-top: 0.2rem;
    max-width: 46rem;
    overflow-wrap: anywhere;
}
.row-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
    margin-top: 0.3rem;
    min-width: 0;
    font-size: 0.72rem;
    color: var(--color-slate-400);
}
.row-slug {
    font-family: var(--font-mono, monospace);
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.row-dot { color: var(--color-slate-300); }
.row-tags {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.25rem;
}
.row-tag {
    border: 1px dashed var(--color-amber-300);
    border-radius: 0.2rem;
    color: var(--color-amber-700);
    padding: 0 0.3rem;
    font-size: 0.68rem;
}
</style>
