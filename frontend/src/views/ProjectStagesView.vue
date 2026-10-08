<script setup>
// A project's settings page — for now, the step stages a feature filed under it
// can open, and each stage's prompt. `/projects/default` is the global list:
// features with no project read it, and a project copies it on its first write.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import NewStageForm from '../components/projects/NewStageForm.vue'
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
  <div class="pj" data-testid="project-page">
    <Card class="pj-card" data-testid="project-info">
      <h2 class="card-header">{{ title }}</h2>
      <p v-if="isDefault" class="pj-note">
        The global list. A feature with no project reads these stages, and a project
        starts from them the first time its own list changes.
      </p>
      <dl v-else-if="project" class="pj-info">
        <div><dt>Name</dt><dd><router-link :to="`/repos/${encodeURIComponent(project.name)}`" class="pj-link" data-testid="project-info-repo">{{ project.name }}</router-link></dd></div>
        <div><dt>Path</dt><dd><code>{{ project.path }}</code></dd></div>
      </dl>
      <p v-else-if="projectsError" class="pj-fail" role="alert">{{ projectsError }}</p>
    </Card>

    <Card class="pj-card" data-testid="project-stages">
      <div class="pj-head">
        <h2 class="card-header pj-title">Stages</h2>
        <span v-if="inherited && !isDefault" class="pj-note" data-testid="project-stages-inherited">
          using the default stages — the first change gives this project its own copy
        </span>
      </div>
      <p v-if="loading && !stages.length" class="pj-note">Loading…</p>
      <p v-else-if="error" class="pj-fail" role="alert" data-testid="project-stages-error">{{ error }}</p>
      <template v-else>
        <ol class="pj-list" data-testid="project-stages-live">
          <StageRow
            v-for="(stage, i) in live" :key="stage.key"
            :stage="stage" :can-write="canWrite" :busy="busy"
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
          <h3 class="pj-sub">Archived</h3>
          <ul class="pj-list" data-testid="project-stages-archived">
            <StageRow v-for="stage in archived" :key="stage.key" :stage="stage" :can-write="canWrite" />
          </ul>
        </template>
      </template>
      <NewStageForm v-if="canWrite && !error" ref="form" class="pj-new" :busy="busy" :error="addError" @add="add" />
    </Card>
  </div>
</template>

<style scoped>
.pj { display: flex; flex-direction: column; gap: 1rem; min-width: 0; }
.pj-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.75rem; margin-bottom: 0.6rem; }
.pj-title { margin-bottom: 0; }
.pj-note { font-size: 12px; color: var(--color-fg-muted); }
.pj-fail { font-size: 12px; color: var(--color-danger); }
.pj-info { display: flex; flex-wrap: wrap; gap: 0.4rem 2rem; font-size: 13px; }
.pj-info div { display: flex; gap: 0.5rem; align-items: baseline; min-width: 0; }
.pj-info dt { color: var(--color-fg-muted); }
.pj-info dd { min-width: 0; overflow-wrap: anywhere; }
.pj-link { color: var(--color-info); text-decoration: none; }
.pj-link:hover { text-decoration: underline; }
.pj-list { border: 1px solid var(--color-border); border-radius: var(--radius-lg); overflow: hidden; }
.pj-list :deep(.is-dragging) { opacity: 0.4; }
.pj-sub { margin: 0.9rem 0 0.4rem; font-size: 12px; font-weight: 600; color: var(--color-fg-subtle); }
.pj-new { margin-top: 0.9rem; }
</style>
