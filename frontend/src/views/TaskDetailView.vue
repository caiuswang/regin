<script setup>
// One task, at its own URL.
//
// The sheets on the list are for acting; this is for reading and linking. A
// task referenced from a commit, an inbox message or a blocking-dependency
// list needs an address, and a modal has none.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import Card from '../components/Card.vue'
import CopyId from '../components/ui/CopyId.vue'
import Icon from '../components/ui/Icon.vue'
import TaskArmSheet from '../components/tasks/TaskArmSheet.vue'
import TaskBodyEditor from '../components/tasks/TaskBodyEditor.vue'
import TaskComments from '../components/tasks/TaskComments.vue'
import TaskGrain from '../components/tasks/TaskGrain.vue'
import TaskImageGallery from '../components/tasks/TaskImageGallery.vue'
import TaskShipControl from '../components/tasks/TaskShipControl.vue'
import TaskDepsEditor from '../components/tasks/TaskDepsEditor.vue'
import TaskEditSheet from '../components/tasks/TaskEditSheet.vue'
import TaskExternalLinks from '../components/tasks/TaskExternalLinks.vue'
import TaskPriorityIcon from '../components/tasks/TaskPriorityIcon.vue'
import TaskRefusal from '../components/tasks/TaskRefusal.vue'
import TaskDefects from '../components/tasks/TaskDefects.vue'
import TaskRunControls from '../components/tasks/TaskRunControls.vue'
import TaskRunHistory from '../components/tasks/TaskRunHistory.vue'
import { priorityMeta, taskRef } from '../utils/taskMeta.js'
import { taskStatusColor } from '../composables/useBadgeColor.js'
import { useConfirm } from '../composables/useConfirm.js'
import { useProjects } from '../composables/useProjects.js'
import { useTaskExec } from '../composables/useTaskExec.js'
import { useTaskImages } from '../composables/useTaskImages.js'
import { useTaskMutations } from '../composables/useTasks.js'

const router = useRouter()
const route = useRoute()
const { byId, ensure: ensureProjects } = useProjects()
const { confirm } = useConfirm()
const { remove } = useTaskMutations()
const { items: imageItems, bind: bindImages, add: addImages } = useTaskImages()

const task = ref(null)
const loading = ref(true)
const error = ref('')
const sheet = ref('')

const id = computed(() => Number(route.params.id))
const project = computed(() => task.value ? byId(task.value.project_id) : null)

const user = api.getStoredUser()
// Read off the stored role here, never off a field in the payload — a payload
// that varied by role would make one cache entry wrong for the next reader.
const canWrite = computed(() => user?.role !== 'viewer')

// Which grain this task is, in two counts, served by the detail route itself.
// A feature holds no runs, ever, so `runs` is what decides whether the
// done-claim is offered at all — the same thing the server checks before it
// refuses one — and `children` decides whether there is a run list to fetch.
const grain = ref({ children: 0, runs: 0 })

// The feature key this task holds, resolved server-side: a linked task is a
// run of another tracker's issue and its key is the link's, not `regin:<id>`.
const featureKey = ref('')

// The same three controls the list row offers, against the same routes; a
// task read at its own address should be startable there.
const { refusals, liveness, busyId, control, syncLiveness } = useTaskExec(load)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/tasks/${id.value}`)
    task.value = data.task
    // The detail read already carries `task.images` (`_one_with_images`), so
    // binding here costs no extra round trip for the list — only the bytes.
    bindImages(data.task)
    // Two counts off the same read. A second request for the runs would be a
    // round trip to learn a number the row's own route already knows.
    grain.value = data.grain || { children: 0, runs: 0 }
    featureKey.value = data.feature_key || ''
    await syncLiveness([data.task])
  } catch (e) {
    error.value = e.status === 404
      ? `No task ${taskRef({ id: id.value })}.`
      : (e.message || String(e))
  } finally {
    loading.value = false
  }
}

// Back to the list rather than staying on an address that can now only 404.
async function deleteTask() {
  const ok = await confirm(
    'Delete task',
    `Delete “${task.value.title}” and its run history, images and links? `
    + 'This cannot be undone — archive it instead to keep it.', true)
  if (!ok) return
  if (await remove(task.value)) router.push('/tasks')
}

// The claim's own response carries the row, so the page takes it rather than
// re-reading — a second GET would show the same fact a moment later and could
// disagree with it if something else wrote in between.
function onShipped(updated) {
  if (updated) task.value = updated
}

// The idx of each image this gesture actually added, by difference: the shared
// composable reports only whether it claimed the files, and the reference
// written into the body is made of the index.
async function attachImages(source) {
  const before = new Set(imageItems.value.map(img => img.idx))
  await addImages(source)
  return imageItems.value
    .map(img => img.idx)
    .filter(idx => idx !== null && idx !== undefined && !before.has(idx))
}

watch(id, load)
onMounted(() => { ensureProjects(); load() })
</script>

<template>
  <div class="space-y-4">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">
          <router-link to="/tasks" class="td-back">
            <Icon name="chevron-left" :size="12" />
            Tasks
          </router-link>
        </div>
        <h1 class="page-title">
          <span class="td-ref">{{ taskRef({ id }) }}</span>
          {{ task?.title || '…' }}
        </h1>
      </div>
      <div v-if="task" class="td-actions">
        <TaskRunControls
          v-if="!grain.children"
          :task="task"
          :liveness="liveness[task.id] || null"
          :busy="busyId === task.id"
          @control="action => control(task, action)"
        />
        <Button variant="secondary" data-testid="task-detail-edit" @click="sheet = 'edit'">
          <Icon name="pencil" :size="13" />
          <span>Edit</span>
        </Button>
        <!-- A parent holds no runs, ever: the server refuses arming one, so
             offering the control here would be a button that always errors. -->
        <Button
          v-if="!grain.children"
          variant="secondary"
          data-testid="task-detail-arm"
          @click="sheet = 'arm'"
        >
          <Icon name="power" :size="13" />
          <span>Arm</span>
        </Button>
        <Button variant="ghost" data-testid="task-detail-deps" @click="sheet = 'deps'">
          <Icon name="workflow" :size="13" />
          <span>Deps</span>
        </Button>
        <Button variant="ghost" data-testid="task-detail-runs" @click="sheet = 'runs'">
          <Icon name="list" :size="13" />
          <span>Runs</span>
        </Button>
        <Button
          variant="ghost"
          class="td-danger"
          data-testid="task-detail-delete"
          @click="deleteTask"
        >
          <Icon name="trash" :size="13" />
          <span>Delete</span>
        </Button>
      </div>
    </header>

    <div v-if="loading" class="empty-state">Loading…</div>
    <div v-else-if="error" class="empty-state" role="alert" data-testid="task-detail-error">
      {{ error }}
    </div>

    <template v-else-if="task">
      <TaskRefusal
        v-if="refusals[task.id]"
        :refusal="refusals[task.id]"
        @focus="id => router.push(`/tasks/${id}`)"
      />
      <Card>
        <dl class="td-facts">
          <div class="td-fact">
            <dt>ID</dt>
            <dd><CopyId :value="String(task.id)" label="task id" /></dd>
          </div>
          <div class="td-fact">
            <dt>Status</dt>
            <dd>
              <TaskShipControl
                :task="task" :can-write="canWrite"
                @changed="onShipped"
              />
            </dd>
          </div>
          <div class="td-fact">
            <dt>Priority</dt>
            <dd class="td-priority">
              <TaskPriorityIcon :value="task.priority" />
              {{ priorityMeta(task.priority).label }}
            </dd>
          </div>
          <div class="td-fact">
            <dt>Project</dt>
            <dd>
              <Badge v-if="project" color="cyan" :label="project.name" />
              <span v-else class="td-none">No project</span>
            </dd>
          </div>
          <div class="td-fact">
            <dt>Source</dt>
            <dd>{{ task.source }}</dd>
          </div>
          <div class="td-fact">
            <dt>Created</dt>
            <dd>{{ task.created_at }}</dd>
          </div>
          <div class="td-fact">
            <dt>Updated</dt>
            <dd>{{ task.updated_at }}</dd>
          </div>
          <div v-if="task.tags.length" class="td-fact">
            <dt>Tags</dt>
            <dd class="td-tags">
              <Badge v-for="tag in task.tags" :key="tag" color="purple" :label="tag" />
            </dd>
          </div>
        </dl>
      </Card>

      <!-- Source over a rendered preview, because a task body is an authored
           document a feature's spec lives in AND an input an agent reads back:
           the editor hands back the bytes it was given. Deliberately NOT the
           rule an agent's launch brief follows (c6efe72a) — that one is input
           text full of paths and fenced source, where every `*` and `#` is a
           literal a renderer would eat. -->
      <TaskBodyEditor
        :task="task" :images="imageItems" :can-write="canWrite" :attach="attachImages"
        @saved="load"
      />

      <TaskImageGallery :items="imageItems" />

      <TaskGrain
        :task="task" :child-count="grain.children" :run-count="grain.runs"
        :can-write="canWrite"
        @attached="load"
      />

      <TaskExternalLinks :task-id="task.id" />

      <TaskDefects
        :task="task" :feature-key="featureKey" :can-write="canWrite"
      />

      <TaskComments :task-id="task.id" :can-write="canWrite" />

      <Card v-if="task.verify_cmd || Object.keys(task.params || {}).length">
        <h2 class="card-header">Execution parameters</h2>
        <pre class="td-params">{{ JSON.stringify(
          { ...task.params, verify_cmd: task.verify_cmd, autorun: task.autorun },
          null, 2) }}</pre>
      </Card>

      <TaskEditSheet
        v-if="sheet === 'edit'"
        :task="task"
        @close="sheet = ''"
        @saved="() => { sheet = ''; load() }"
      />
      <TaskArmSheet
        v-else-if="sheet === 'arm'"
        :task="task"
        @close="sheet = ''"
        @armed="() => { sheet = ''; load() }"
      />
      <TaskDepsEditor
        v-else-if="sheet === 'deps'"
        :task="task"
        @close="sheet = ''"
        @saved="() => { sheet = ''; load() }"
      />
      <TaskRunHistory
        v-else-if="sheet === 'runs'"
        :task="task"
        @close="sheet = ''"
      />
    </template>
  </div>
</template>

<style scoped>
.td-back {
    display: inline-flex;
    align-items: center;
    gap: 0.125rem;
    color: var(--color-fg-subtle);
    text-decoration: none;
}
.td-back:hover { color: var(--color-fg); }
.td-ref {
    font-family: var(--font-mono, ui-monospace, monospace);
    color: var(--color-fg-faint);
    margin-right: 0.375rem;
}
.td-actions { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.td-danger { color: var(--color-danger); }
.td-facts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.875rem;
}
.td-fact dt {
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-fg-faint);
    margin-bottom: 0.25rem;
}
.td-fact dd {
    font-size: 0.8125rem;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.td-priority { display: flex; align-items: center; gap: 0.375rem; }
.td-tags { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.td-none { color: var(--color-fg-faint); }
.td-params {
    font-size: 0.75rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    color: var(--color-fg-muted);
    background: var(--color-surface-2);
    padding: 0.75rem;
    border-radius: var(--radius-md);
    overflow-x: auto;
}
</style>
