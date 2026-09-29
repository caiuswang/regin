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
import TaskImageGallery from '../components/tasks/TaskImageGallery.vue'
import TaskDepsEditor from '../components/tasks/TaskDepsEditor.vue'
import TaskEditSheet from '../components/tasks/TaskEditSheet.vue'
import TaskFeature from '../components/tasks/TaskFeature.vue'
import TaskKindChip from '../components/tasks/TaskKindChip.vue'
import TaskPriorityIcon from '../components/tasks/TaskPriorityIcon.vue'
import TaskRefusal from '../components/tasks/TaskRefusal.vue'
import FeatureDefects from '../components/features/FeatureDefects.vue'
import TaskPrimaryAction from '../components/tasks/TaskPrimaryAction.vue'
import TaskVerbMenu from '../components/tasks/TaskVerbMenu.vue'
import TaskRunHistory from '../components/tasks/TaskRunHistory.vue'
import { priorityMeta, taskRef } from '../utils/taskMeta.js'
import { localStamp, utcTitle } from '../utils/time.js'
import { taskStatusColor } from '../composables/useBadgeColor.js'
import { useConfirm } from '../composables/useConfirm.js'
import { useProjects } from '../composables/useProjects.js'
import { useTaskVerbs } from '../composables/useTaskVerbs.js'
import { useAttachedImages } from '../composables/useAttachedImages.js'
import { useTaskMutations } from '../composables/useTasks.js'
import { rememberedQuery } from '../composables/usePipelineTabState'
import { useDetailBack } from '../composables/useDetailBack'
import { usePipelineLive } from '../composables/usePipelineEvents'

const router = useRouter()
const route = useRoute()
const { byId, ensure: ensureProjects } = useProjects()
const { confirm } = useConfirm()
const { remove } = useTaskMutations()
const {
  items: imageItems, busy: imagesBusy, error: imagesError,
  bind: bindImages, add: addImages, remove: removeImage,
} = useAttachedImages()

const task = ref(null)
const loading = ref(true)
const error = ref('')
const sheet = ref('')

const id = computed(() => Number(route.params.id))

// The list you opened this row from, not the default one: the back link
// carries the query the tab was last left under, so a filter, a search or a
// scope survives the round trip. An arrival by deep link has none, and the
// bare path is the right answer there.
const back = useDetailBack(() => ({ to: { path: '/tasks', query: rememberedQuery('/tasks') }, label: 'Tasks' }))

const project = computed(() => task.value ? byId(task.value.project_id) : null)

const user = api.getStoredUser()
// Read off the stored role here, never off a field in the payload — a payload
// that varied by role would make one cache entry wrong for the next reader.
const canWrite = computed(() => user?.role !== 'viewer')

// The feature this task is a step of, resolved server-side and carrying its
// own title — the authority for what is being delivered. A task's title says
// what this step does, and the two diverge.
const feature = ref(null)
const featureTitle = computed(() => feature.value?.title || task.value?.feature_title || '')

// What a step with no written instructions is for — its kind says it, and an
// empty body would otherwise read as a step with nothing to do.
const DELIVERABLE = {
  spec: fid => 'Deliverable: the feature\'s spec, written onto the feature with '
    + `regin feature spec ${fid} --file -. No code changes; the run ends by `
    + 'finishing so the operator can approve.',
  build: () => 'Deliverable: code that makes the feature spec\'s acceptance items green.',
  fix: () => 'Deliverable: the linked defect closed.',
}
const deliverable = computed(
  () => DELIVERABLE[task.value?.kind]?.(task.value.feature_id) || '')

// The parameters the NEXT attempt will spend. They used to be three columns on
// the task; an attempt is what spends them, so they live on an `armed` run row
// and this is that row.
const armed = ref(null)

// The same primary action and ⋯ menu every other surface offers for this
// state (feature #119 R4a), through the same routes.
const { refusals, liveness, busyId, act: actOn, syncLiveness } = useTaskVerbs(load, {
  openSheet: (kind) => { sheet.value = kind },
})

// Delete leaves an address that can now only 404, so it is the one verb the
// page answers itself.
function act(verb) {
  return verb === 'delete' ? deleteTask() : actOn(verb, task.value)
}

// Archive/Restore is the header's, and ⋯ omits it; drawn only where the
// server's menu for this state offers it — a running task cannot be archived.
const shelfVerb = computed(() => {
  const menu = task.value?.menu || []
  return ['restore', 'archive'].find(v => menu.includes(v)) || ''
})

// `quiet` keeps the page on screen: an event re-read must not flash "Loading…".
// An event re-read can still be in flight when the route moves to another
// task, so every answer is checked against the id it was asked for.
async function load({ quiet = false } = {}) {
  const want = id.value
  if (!quiet) loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/tasks/${want}`)
    if (want !== id.value) return
    task.value = data.task
    // The detail read already carries `task.images` (`_one_with_images`), so
    // binding here costs no extra round trip for the list — only the bytes.
    // Not on a quiet re-read: binding resets the strip and re-fetches every blob.
    if (!quiet) bindImages(data.task)
    // Both off the same read. A second request would be a round trip to learn
    // something the row's own route already knows.
    feature.value = data.feature || null
    armed.value = data.armed || null
    await syncLiveness([data.task])
  } catch (e) {
    if (want !== id.value) return
    error.value = e.status === 404
      ? `No task ${taskRef({ id: want })}.`
      : (e.message || String(e))
  } finally {
    if (want === id.value) loading.value = false
  }
}

// Back to the list rather than staying on an address that can now only 404.
async function deleteTask() {
  const ok = await confirm(
    'Delete task',
    `Delete “${task.value.title}” and its run history, images and links? `
    + 'This cannot be undone — archive it instead to keep it.', true)
  if (!ok) return
  if (await remove(task.value)) back.leave()
}

watch(id, () => load())
usePipelineLive(() => load({ quiet: true }))
onMounted(() => { ensureProjects(); load() })
</script>

<template>
  <div class="space-y-4">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">
          <router-link v-slot="{ href }" :to="back.to.value" custom>
            <a :href="href" class="td-back focus-visible:outline-2 focus-visible:outline-blue-500" data-testid="detail-back" @click="back.go">
              <Icon name="chevron-left" :size="12" />
              {{ back.label.value }}
            </a>
          </router-link>
        </div>
        <h1 class="page-title">
          <span class="td-ref">{{ taskRef({ id }) }}</span>
          {{ task?.title || '…' }}
        </h1>
        <div v-if="task && (task.feature_id || deliverable)" class="td-kind">
          <TaskKindChip :kind="task.kind" />
          <router-link
            v-if="task.feature_id" class="td-feature" :to="`/features/${task.feature_id}`"
            data-testid="task-detail-feature"
          >#{{ task.feature_id }} {{ featureTitle }}</router-link>
        </div>
      </div>
      <div v-if="task" class="td-actions" data-testid="task-detail-actions">
        <TaskPrimaryAction
          :task="task"
          :liveness="liveness[task.id] || null"
          :busy="busyId === task.id"
          :can-write="canWrite"
          @verb="act"
        />
        <Button variant="ghost" data-testid="task-detail-runs" @click="sheet = 'runs'">
          <Icon name="list" :size="13" />
          <span>Runs</span>
        </Button>
        <Button
          v-if="canWrite && shelfVerb"
          variant="ghost"
          :disabled="busyId === task.id"
          :data-testid="`task-detail-${shelfVerb}`"
          @click="act(shelfVerb)"
        >
          <Icon :name="shelfVerb === 'archive' ? 'archive' : 'refresh-cw'" :size="13" />
          <span>{{ shelfVerb === 'archive' ? 'Archive' : 'Restore' }}</span>
        </Button>
        <TaskVerbMenu
          v-if="canWrite" :task="task" :busy="busyId === task.id"
          :omit="['archive', 'restore']" @verb="act"
        />
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
            <!-- The execution lifecycle, and only that. The done-claim moved
                 to the feature's own page: a task's status is what a run
                 settling writes, and one control carrying both axes is what
                 let a settling run ship a feature nobody claimed. -->
            <dd><Badge :color="taskStatusColor(task.status)"
                       :label="task.status" /></dd>
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
          <div v-if="task.fixes_defects?.length" class="td-fact">
            <dt>Fixes</dt>
            <dd class="td-tags" data-testid="task-fixes-defects">
              <router-link v-for="d in task.fixes_defects" :key="d"
                           class="td-defect" :to="`/defects/${d}`"
              >defect #{{ d }}</router-link>
            </dd>
          </div>
          <div class="td-fact">
            <dt>Source</dt>
            <dd>{{ task.source }}</dd>
          </div>
          <div class="td-fact">
            <dt>Created</dt>
            <dd :title="utcTitle(task.created_at)">{{ localStamp(task.created_at) }}</dd>
          </div>
          <div class="td-fact">
            <dt>Updated</dt>
            <dd :title="utcTitle(task.updated_at)">{{ localStamp(task.updated_at) }}</dd>
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
        :task="task" :images="imageItems" :can-write="canWrite" :attach="addImages"
        :empty-text="deliverable" @saved="load"
      />

      <TaskImageGallery
        :items="imageItems"
        :busy="imagesBusy"
        :error="imagesError"
        :can-write="canWrite"
        @pick="addImages"
        @remove="removeImage"
      />

      <TaskFeature
        :task-id="task.id" :feature="feature" :can-write="canWrite"
        @changed="load"
      />

      <FeatureDefects :feature="feature" :fix-task-id="task.id" :can-write="canWrite" />

      <TaskComments :task-id="task.id" :can-write="canWrite" />

      <!-- The pending attempt's parameters, not the task's: a rerun under
           different ones is a second run row, which is what retired
           "a sub-task is a rerun". -->
      <Card v-if="armed">
        <h2 class="card-header">Next attempt</h2>
        <pre class="td-params">{{ JSON.stringify(
          { ...armed.params, verify_cmd: armed.verify_cmd,
            autorun: armed.autorun },
          null, 2) }}</pre>
      </Card>

      <TaskEditSheet
        v-if="sheet === 'edit'"
        :task="{ ...task, feature_title: featureTitle }"
        @close="sheet = ''"
        @saved="() => { sheet = ''; load() }"
      />
      <!-- The pending attempt rides in with the task: without it the sheet
           seeds from the last arm remembered in this browser, and re-arming
           would silently re-aim the attempt at another task's branch. -->
      <TaskArmSheet
        v-else-if="sheet === 'arm'"
        :task="{ ...task, armed }"
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
.td-kind {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin-top: 0.25rem;
    min-width: 0;
}
.td-feature {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.td-feature:hover { color: var(--color-primary); }
.td-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem; }
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
.td-defect { font-family: var(--font-mono, monospace); color: var(--color-primary); }
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
