<script setup>
// The tasks that are this feature's steps — listed, and captured.
//
// The edge existed in the data layer the whole time (`agent_tasks.feature_id`
// is a single column, and `GET /api/tasks?feature_id=` already filters on it);
// what was missing was the direction. Without it the path that starts at a
// feature broke at its second step, and the operator had to begin at the task
// board and work backwards.
//
// Listing is scoped SERVER-side. A client-side filter over one keyset page
// answers a different question than this heading claims to ask.
//
// No Arm and no Run here, deliberately. Execution parameters and run control
// stay on the task page — a second copy of the run machinery would be a second
// writer of the same lifecycle. Each row is a link to where that work happens.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import Textarea from '../ui/Textarea.vue'
import TaskKindChip from '../tasks/TaskKindChip.vue'
import TaskStatusIcon from '../tasks/TaskStatusIcon.vue'
import { carriedProject, projectOptions, useProjects }
  from '../../composables/useProjects.js'

const props = defineProps({
  featureId: { type: Number, required: true },
  canWrite: { type: Boolean, default: false },
  projectId: { type: Number, default: null },
})
const { items: projects, ensure: ensureProjects } = useProjects()
const router = useRouter()

const items = ref([])
const loading = ref(true)
const loadError = ref('')
const open = ref(false)
const title = ref('')
const busy = ref(false)
const failed = ref('')
const projectId = ref('')
const stepKind = ref('')
const stepBody = ref('')
const newKind = ref('task')

// A free-form step may declare any stage but fix: a fix step belongs to a
// defect and is made from the defect, never typed here.
const STAGE_OPTIONS = [
  { value: 'task', label: 'Task' },
  { value: 'spec', label: 'Spec' },
  { value: 'build', label: 'Build' },
]

// Server order is kept within a kind: Array.prototype.sort is stable.
const KIND_ORDER = { spec: 0, build: 1, fix: 2 }
const rank = (t) => KIND_ORDER[t.kind] ?? 3
const ordered = computed(() => [...items.value].sort((a, b) => rank(a) - rank(b)))

// One spec step and one build step per feature: a verb whose step exists is a
// link to it, not a second one. Matched by the verb's own key, not by kind —
// transcribed slice tasks are `build` too, and are not the Build step.
function startStep(kind) {
  const key = `feature:${props.featureId}:${kind}`
  const existing = items.value.find(t => t.external_id === key)
  if (existing) return router.push(`/tasks/${existing.id}`)
  open.value = false
  failed.value = ''
  stepBody.value = ''
  stepKind.value = kind
}

async function submitStep() {
  if (busy.value) return
  busy.value = true
  failed.value = ''
  const res = await api.post(`/features/${props.featureId}/steps`, {
    kind: stepKind.value, body: stepBody.value.trim() || null,
  })
  busy.value = false
  if (res?.ok === false || !res?.task) {
    failed.value = res?.msg || 'the step was refused'
    return
  }
  stepKind.value = ''
  router.push(`/tasks/${res.task.id}`)
}

// The feature's project is the starting value, not a rule: a feature that
// spans repos files each step under its own, so the pick stays editable and
// is always sent — "No project" included.
async function startNew() {
  stepKind.value = ''
  newKind.value = 'task'
  open.value = true
  await ensureProjects()
  projectId.value = carriedProject(props.projectId, projects.value)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/tasks?feature_id=${props.featureId}`)
    items.value = body.items || []
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(() => props.featureId, load, { immediate: true })
defineExpose({ load })

// Re-read rather than splice the new row in: the list is ordered and filtered
// by the server, and a row placed here by hand would sit wherever the client
// guessed rather than where the next reload will put it.
async function submit() {
  const name = title.value.trim()
  if (!name || busy.value) return
  busy.value = true
  failed.value = ''
  const res = await api.post('/tasks', {
    title: name, feature_id: props.featureId, kind: newKind.value,
    project_id: projectId.value ? Number(projectId.value) : null,
  })
  busy.value = false
  if (res?.ok === false || !res?.task) {
    failed.value = res?.msg || 'the task was refused'
    return
  }
  title.value = ''
  open.value = false
  await load()
}
</script>

<template>
  <Card data-testid="feature-tasks">
    <div class="ft-head">
      <div>
        <h2 class="card-header ft-title">Tasks</h2>
        <p class="ft-sub">the steps of this feature — arm and run them on their own pages</p>
      </div>
      <div v-if="canWrite" class="ft-verbs">
        <Button
          variant="secondary" size="sm" data-testid="feature-verb-spec"
          @click="startStep('spec')"
        >Write spec</Button>
        <Button
          variant="secondary" size="sm" data-testid="feature-verb-build"
          @click="startStep('build')"
        >Build</Button>
        <Button
          v-if="!open"
          variant="ghost" size="sm" class="ft-free" data-testid="feature-new-task"
          @click="startNew"
        >
          <Icon name="plus" :size="11" />
          task
        </Button>
      </div>
    </div>

    <form v-if="stepKind" class="ft-form" @submit.prevent="submitStep">
      <label class="ft-label" :for="`ft-step-${featureId}`">
        Instructions (optional)
      </label>
      <Textarea
        :id="`ft-step-${featureId}`"
        v-model="stepBody" :rows="3" :disabled="busy"
        :placeholder="stepKind === 'spec' ? 'what the spec must settle' : 'what the build must respect'"
        data-testid="feature-step-body"
      />
      <div class="ft-actions">
        <Button
          type="submit" variant="primary" size="sm" :disabled="busy"
          data-testid="feature-step-submit"
        >{{ busy ? 'Creating…' : 'Create' }}</Button>
        <Button
          type="button" variant="ghost" size="sm" :disabled="busy"
          @click="stepKind = ''"
        >Cancel</Button>
      </div>
    </form>

    <form v-if="open" class="ft-form" @submit.prevent="submit">
      <label class="ft-label" :for="`ft-title-${featureId}`">
        What this step does
      </label>
      <Input
        :id="`ft-title-${featureId}`"
        v-model="title" :disabled="busy"
        placeholder="a step of this feature"
        data-testid="feature-task-title"
      />
      <span class="ft-label">Stage</span>
      <Select
        v-model="newKind" block :disabled="busy"
        :options="STAGE_OPTIONS"
        aria-label="Stage"
        data-testid="feature-task-kind"
      />
      <span class="ft-label">Project</span>
      <Select
        v-model="projectId" block :disabled="busy"
        :options="projectOptions(projects)"
        aria-label="Project"
        data-testid="feature-task-project"
      />
      <div class="ft-actions">
        <Button
          type="submit" variant="secondary" size="sm"
          :disabled="busy || !title.trim()" data-testid="feature-task-submit"
        >{{ busy ? 'Capturing…' : 'Capture' }}</Button>
        <Button
          type="button" variant="ghost" size="sm" :disabled="busy"
          @click="open = false"
        >Cancel</Button>
      </div>
    </form>

    <p v-if="failed" class="ft-fail" role="status" data-testid="feature-task-error">
      {{ failed }}
    </p>

    <p v-if="loading" class="ft-note">Loading…</p>
    <p v-else-if="loadError" class="ft-fail" role="alert">{{ loadError }}</p>
    <p v-else-if="!items.length" class="ft-note" data-testid="feature-tasks-empty">
      No task is placed under this feature. It can still be shipped and blamed —
      it simply has nothing for an agent to run.
    </p>
    <ul v-else class="ledger-list" data-testid="feature-tasks-list">
      <li v-for="task in ordered" :key="task.id" class="ledger-row ft-row">
        <router-link class="ft-link" :to="`/tasks/${task.id}`">
          <TaskStatusIcon :status="task.status" :size="13" />
          <TaskKindChip :kind="task.kind" />
          <span class="ledger-id">#{{ task.id }}</span>
          <span class="ft-name">{{ task.title }}</span>
        </router-link>
        <router-link
          v-for="d in task.fixes_defects || []" :key="d"
          class="ledger-tag" :to="`/defects/${d}`"
          :data-testid="`feature-task-fixes-${task.id}-${d}`"
        >fixes #{{ d }}</router-link>
        <span v-if="task.run_count" class="ft-runs">{{ task.run_count }} runs</span>
      </li>
    </ul>
  </Card>
</template>

<style scoped>
.ft-head {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem; margin-bottom: 0.6rem;
}
.ft-title { margin-bottom: 0; }
.ft-verbs { display: flex; align-items: center; gap: 0.35rem; flex: none; }
.ft-free { font-size: 11px; color: var(--color-fg-muted); }
.ft-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.ft-form { display: flex; flex-direction: column; gap: 0.35rem; }
.ft-label { font-size: 11.5px; color: var(--color-fg-muted); }
.ft-actions { display: flex; gap: 0.4rem; }
.ft-note { margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-fg-muted); }
.ft-fail {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
.ft-row { display: flex; align-items: center; gap: 0.5rem; }
.ft-link {
    display: flex; align-items: center; gap: 0.5rem; min-width: 0; flex: 1 1 auto;
    text-decoration: none; color: var(--color-fg);
}
.ft-link:focus-visible { outline: 2px solid var(--color-blue-500); }
.ft-name {
    font-size: 0.8125rem; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
}
.ft-runs { font-size: 11px; color: var(--color-fg-subtle); flex: none; }
</style>
