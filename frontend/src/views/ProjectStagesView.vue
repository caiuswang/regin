<script setup>
// A project's settings page — the language its task runs write in, the step
// stages a feature filed under it can open, and each stage's prompt. `/projects/default` is the global list:
// features with no project read it, and a project copies it on its first write.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import NewStageForm from '../components/projects/NewStageForm.vue'
import OutputLanguageField from '../components/projects/OutputLanguageField.vue'
import StageRow from '../components/projects/StageRow.vue'
import { useConfirm } from '../composables/useConfirm'
import { useFlash } from '../composables/useFlash'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { useProjectStages, stagesPath } from '../composables/useProjectStages'
import { useProjects } from '../composables/useProjects'

const route = useRoute()
const publish = usePipelineHeaderPublisher()
const { confirm } = useConfirm()
const { flash } = useFlash()

const isDefault = computed(() => route.params.id === 'default')
const projectId = computed(() => (isDefault.value ? null : route.params.id))
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')

const { items: projects, ensure: ensureProjects, error: projectsError } = useProjects()
ensureProjects()
const project = computed(() => projects.value.find(p => String(p.id) === projectId.value) || null)
const title = computed(() => {
  if (isDefault.value) return 'Default stages'
  return project.value?.name || `project #${projectId.value}`
})
watch(title, (t) => publish({ subline: `${t} — the steps a feature can open, and their prompts.` }),
  { immediate: true })

const { stages, live, inherited, loading, error, load } = useProjectStages(projectId)
const archived = computed(() => stages.value.filter(s => s.archived_at))

const busy = ref(false)
const addError = ref('')
const form = ref(null)

// A write's answer is applied only while the page still names the project it
// was sent for.
async function write(request, { onDone } = {}) {
  if (busy.value) return null
  const sentFor = projectId.value
  busy.value = true
  try {
    const res = await request()
    if (res?.ok === false) {
      flash(res.msg || 'the change was refused', 'error')
      return null
    }
    if (sentFor === projectId.value) onDone?.(res)
    return res
  } catch (e) {
    flash(e?.message || String(e), 'error')
    return null
  } finally {
    busy.value = false
  }
}

const base = () => stagesPath(projectId.value)
const one = key => `${base()}/${encodeURIComponent(key)}`

async function add(payload) {
  addError.value = ''
  busy.value = true
  try {
    const res = await api.post(base(), payload)
    if (res?.ok === false) { addError.value = res.msg || 'the stage was refused'; return }
    form.value?.reset()
    await load()
  } finally {
    busy.value = false
  }
}

// A refused edit re-reads too, so a row drops the draft the server would not take.
async function patch(key, body) {
  if (!(await write(() => api.patch(one(key), body), { onDone: load }))) load()
}

async function remove(stage) {
  const ok = await confirm(`Remove ${stage.label}?`,
    'A stage no task was opened from is deleted. One with tasks is archived: its button goes, its tasks stay.',
    true)
  if (!ok) return
  await write(() => api.del(one(stage.key)), {
    onDone: (res) => {
      flash(res.archived ? `${stage.label} archived` : `${stage.label} deleted`, 'success')
      load()
    },
  })
}

function reorder(keys) {
  return write(() => api.put(`${base()}/order`, { keys }), {
    onDone: (res) => { stages.value = res.stages || stages.value; inherited.value = !!res.inherited },
  })
}

function moveTo(key, index) {
  const keys = live.value.map(s => s.key).filter(k => k !== key)
  keys.splice(Math.max(0, Math.min(index, keys.length)), 0, key)
  if (keys.join() === live.value.map(s => s.key).join()) return
  reorder(keys)
}

function nudge(stage, delta) {
  moveTo(stage.key, live.value.findIndex(s => s.key === stage.key) + delta)
}

const dragging = ref(null)
function onDragStart(stage, event) {
  dragging.value = stage.key
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', stage.key)
}
function onDrop(target) {
  const key = dragging.value
  dragging.value = null
  if (!key || key === target.key) return
  moveTo(key, live.value.findIndex(s => s.key === target.key))
}
</script>

<template>
  <div class="pj" data-testid="project-page" data-pl-part="settings">
    <section class="pl-panel pj-panel" data-pl-part="panel" data-testid="project-info" aria-labelledby="pj-title">
      <header class="pl-panel__head pj-head" data-pl-part="panel-head">
        <router-link to="/overview" class="pl-panel__link pj-back" data-pl-part="panel-link" data-pl-back>Overview</router-link>
        <h2 id="pj-title" class="pl-panel__title pj-name" data-pl-part="panel-title">{{ title }}</h2>
        <span class="pl-panel__sub" data-pl-part="panel-meta">{{ isDefault ? 'default stages' : 'project settings' }}</span>
        <router-link
          v-if="project" :to="`/repos/${encodeURIComponent(project.name)}`"
          class="pl-panel__link pj-repo" data-pl-part="panel-link" data-testid="project-info-repo"
        >Open repo →</router-link>
      </header>
      <p v-if="isDefault" class="pj-line pj-note" data-pl-part="note">
        The global list. A feature with no project reads these stages, and a project
        starts from them the first time its own list changes.
      </p>
      <dl v-else-if="project" class="pj-line pj-info">
        <dt data-pl-part="meta-label">Path</dt>
        <dd><code class="pj-path" data-testid="project-info-path">{{ project.path }}</code></dd>
      </dl>
      <p v-else-if="projectsError" class="pj-line pj-fail" role="alert">{{ projectsError }}</p>
      <div v-if="isDefault || project" class="pj-line">
        <OutputLanguageField :project-id="projectId" :can-write="canWrite" />
      </div>
    </section>

    <section class="pl-panel pj-panel" data-pl-part="panel" data-testid="project-stages" aria-labelledby="pj-stages-title">
      <header class="pl-panel__head" data-pl-part="panel-head">
        <h2 id="pj-stages-title" class="pl-panel__title" data-pl-part="panel-title">Stages</h2>
        <span class="pl-panel__sub" data-pl-part="panel-meta" data-testid="project-stages-count">
          {{ live.length }} {{ live.length === 1 ? 'step' : 'steps' }} a feature can open
        </span>
        <span
          v-if="inherited && !isDefault" class="pj-inherited" data-pl-part="tag" data-pl-dashed
          data-testid="project-stages-inherited"
          title="The first change gives this project its own copy"
        >inherits the defaults</span>
      </header>
      <p v-if="loading && !stages.length" class="pl-panel__empty" data-pl-part="panel-empty">Loading…</p>
      <p v-else-if="error" class="pj-line pj-fail" role="alert" data-testid="project-stages-error">{{ error }}</p>
      <template v-else>
        <ol class="pj-list" data-testid="project-stages-live">
          <StageRow
            v-for="(stage, i) in live" :key="stage.key"
            :stage="stage" :position="i + 1" :can-write="canWrite" :busy="busy"
            :first="i === 0" :last="i === live.length - 1"
            :class="{ 'is-dragging': dragging === stage.key }"
            :draggable="canWrite && !busy"
            @dragstart="onDragStart(stage, $event)"
            @dragend="dragging = null"
            @dragover.prevent
            @drop.prevent="onDrop(stage)"
            @rename="patch(stage.key, { label: $event })"
            @role="patch(stage.key, { role: $event })"
            @prompt="patch(stage.key, { prompt: $event })"
            @remove="remove(stage)"
            @move="nudge(stage, $event)"
          />
        </ol>
        <template v-if="archived.length">
          <h3 class="pj-sub" data-pl-part="group-label">Archived · tasks kept, button gone</h3>
          <ul class="pj-list" data-testid="project-stages-archived">
            <StageRow v-for="stage in archived" :key="stage.key" :stage="stage" :can-write="canWrite" />
          </ul>
        </template>
      </template>
      <footer v-if="canWrite && !error" class="pl-panel__foot pj-foot" data-pl-part="panel-foot">
        <NewStageForm ref="form" :busy="busy" :error="addError" @add="add" />
      </footer>
    </section>
  </div>
</template>

<style scoped>
.pj { display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.pj-head { flex-wrap: wrap; row-gap: 0.25rem; }
.pj-back { margin-left: 0; }
.pj-name { min-width: 0; overflow-wrap: anywhere; }
.pj-repo { margin-left: auto; }
.pj-line { padding: 0.65rem 1.1rem 0.8rem; border-top: 1px solid var(--color-border-subtle); font-size: 12.5px; }
.pj-note { color: var(--color-fg-muted); }
.pj-fail { color: var(--color-danger); }
.pj-info { display: flex; align-items: baseline; gap: 0.6rem; min-width: 0; }
.pj-info dt { font-size: 11px; font-weight: 600; color: var(--color-fg-subtle); }
.pj-info dd { min-width: 0; overflow-wrap: anywhere; }
.pj-path { font-size: 12px; color: var(--color-fg-muted); }
.pj-inherited {
    font-size: 11px; padding: 0 0.45rem; border: 1px dashed var(--color-border-strong, var(--color-border));
    border-radius: var(--radius-sm); color: var(--color-fg-muted);
}
.pj-list { margin: 0; padding: 0; list-style: none; }
.pj-list :deep(.is-dragging) { opacity: 0.4; }
.pj-sub {
    padding: 0.6rem 1.1rem 0.3rem; border-top: 1px solid var(--color-border);
    font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--color-fg-subtle);
}
.pj-foot { padding: 0.75rem 1.1rem; }
.pj-back::before { content: "←"; margin-right: 0.3rem; }
</style>
