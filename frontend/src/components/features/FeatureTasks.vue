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
//
// No create form either: `+ task` asks the page for the shell's composer, so a
// step captured here carries every field a capture anywhere else does.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import FeatureStepDialog from './FeatureStepDialog.vue'
import FeatureTaskSessions from './FeatureTaskSessions.vue'
import Icon from '../ui/Icon.vue'
import TaskKindChip from '../tasks/TaskKindChip.vue'
import TaskStatusIcon from '../tasks/TaskStatusIcon.vue'
import { TASKS_ANCHOR, tasksGraphLink, tasksListLink } from '../../utils/featureLinks'
import { sessionsByTask } from '../../utils/ledgerFormat'
import { taskTitle } from '../../utils/taskTitle.js'
import { useProjectStages } from '../../composables/useProjectStages'

const props = defineProps({
  featureId: { type: Number, required: true },
  canWrite: { type: Boolean, default: false },
  projectId: { type: Number, default: null },
  // Transcribed slices are the feature's build; a second Build step beside
  // them would be a second way to build one feature.
  hasSlices: { type: Boolean, default: false },
  // The ledger's session rows — the Sessions card's own list, regrouped here
  // by the task whose run produced each one.
  sessions: { type: Array, default: () => [] },
  canOpenTrace: { type: Boolean, default: true },
})
const emit = defineEmits(['counted', 'new-task'])
const router = useRouter()

const items = ref([])
const loading = ref(true)
const loadError = ref('')
const stepOpen = ref(false)
const stepStage = ref(null)
const byTask = computed(() => sessionsByTask(props.sessions))

// Creation order, oldest first, and no regrouping by kind: grouping put a new
// fix task after the build step but ahead of every free-form task, so the row
// just created landed mid-list. In creation order a new step is always the
// last row. The size is the server's ceiling because oldest-first paging
// would push the newest row off the only page this card reads.
const LIST_QUERY = 'sort=created:asc&size=200'

// One button per live stage of the feature's project, in the project's order.
// An archived stage loses its button; the tasks it opened stay listed below.
const { live: liveStages, error: stagesError } = useProjectStages(() => props.projectId)
const stageVerbs = computed(() => liveStages.value
  .filter(stage => !(props.hasSlices && stage.role === 'build')))

// One step per stage per feature: a stage whose step exists is a link to it,
// not a second one. Matched by the stage's own key, not by kind — transcribed
// slice tasks are `build` too, and are not the Build step.
function startStep(stage) {
  const key = `feature:${props.featureId}:${stage.key}`
  const existing = items.value.find(t => t.external_id === key)
  if (existing) return router.push(`/tasks/${existing.id}`)
  stepStage.value = stage
  stepOpen.value = true
}

async function load() {
  const want = props.featureId
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/tasks?feature_id=${want}&${LIST_QUERY}`)
    if (want !== props.featureId) return
    items.value = body.items || []
    const done = items.value.filter(t => t.status === 'done').length
    emit('counted', { done, total: items.value.length })
  } catch (e) {
    if (want === props.featureId) loadError.value = e?.message || String(e)
  } finally {
    if (want === props.featureId) loading.value = false
  }
}
watch(() => props.featureId, load, { immediate: true })
defineExpose({ load })

</script>

<template>
  <Card :id="TASKS_ANCHOR" data-testid="feature-tasks">
    <div class="ft-head">
      <div>
        <h2 class="card-header ft-title">Tasks</h2>
        <p class="ft-sub">the steps of this feature — arm and run them on their own pages</p>
      </div>
      <div class="ft-actions">
        <div class="ft-links">
          <router-link
            :to="tasksListLink(featureId)"
            class="ft-link-out focus-visible:outline-2 focus-visible:outline-blue-500"
            data-testid="feature-tasks-open-list"
          >Open in Tasks</router-link>
          <router-link
            :to="tasksGraphLink(featureId)"
            class="ft-link-out focus-visible:outline-2 focus-visible:outline-blue-500"
            data-testid="feature-tasks-open-graph"
          >Graph</router-link>
        </div>
        <div v-if="canWrite" class="ft-verbs">
          <Button
            v-for="stage in stageVerbs" :key="stage.key"
            variant="secondary" size="sm" :data-testid="`feature-verb-${stage.key}`"
            @click="startStep(stage)"
          >{{ stage.label }}</Button>
          <Button
            variant="ghost" size="sm" class="ft-free" data-testid="feature-new-task"
            @click="emit('new-task')"
          >
            <Icon name="plus" :size="11" />
            task
          </Button>
        </div>
      </div>
    </div>

    <FeatureStepDialog
      v-if="canWrite" v-model:open="stepOpen"
      :feature-id="featureId" :project="props.projectId" :stage="stepStage"
    />
    <p v-if="canWrite && stagesError" class="ft-fail" role="alert" data-testid="feature-stages-error">
      The project's stages could not be read — {{ stagesError }}
    </p>

    <!-- Only before the first read: a push re-read keeps the rows mounted. -->
    <p v-if="loading && !items.length" class="ft-note">Loading…</p>
    <p v-else-if="loadError" class="ft-fail" role="alert">{{ loadError }}</p>
    <p v-else-if="!items.length" class="ft-note" data-testid="feature-tasks-empty">
      No task is placed under this feature. It can still be shipped and blamed —
      it simply has nothing for an agent to run.
    </p>
    <ul v-else class="ledger-list" data-testid="feature-tasks-list">
      <li v-for="task in items" :key="task.id" class="ledger-row ft-row">
        <router-link class="ft-link" :to="`/tasks/${task.id}`">
          <TaskStatusIcon :status="task.status" :size="13" />
          <TaskKindChip :kind="task.kind" />
          <span class="ledger-id">#{{ task.id }}</span>
          <span class="ft-name">{{ taskTitle(task) }}</span>
        </router-link>
        <router-link
          v-if="task.waits_on"
          :to="`/tasks/${task.waits_on.id}`"
          class="ft-waits focus-visible:outline-2 focus-visible:outline-blue-500"
          :data-testid="`feature-task-waits-${task.id}`"
        >waits on #{{ task.waits_on.id }}<template v-if="(task.blocked_by?.length || 0) > 1">+{{ task.blocked_by.length - 1 }}</template></router-link>
        <router-link
          v-for="d in task.fixes_defects || []" :key="d"
          class="ledger-tag" :to="`/defects/${d}`"
          :data-testid="`feature-task-fixes-${task.id}-${d}`"
        >fixes #{{ d }}</router-link>
        <span v-if="task.run_count" class="ft-runs">{{ task.run_count }} runs</span>
        <FeatureTaskSessions
          :task-id="task.id" :rows="byTask.get(task.id) || []"
          :can-open-trace="canOpenTrace"
        />
      </li>
    </ul>
  </Card>
</template>

<style scoped>
.ft-head {
    display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem; margin-bottom: 0.6rem;
}
.ft-title { margin-bottom: 0; }
.ft-verbs { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; min-width: 0; }
.ft-free { font-size: 11px; color: var(--color-fg-muted); }
.ft-actions {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem;
    margin-left: auto; min-width: 0;
}
.ft-links {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    font-size: 12px;
}
.ft-link-out {
    color: var(--color-info); text-decoration: none;
}
.ft-link-out:hover { text-decoration: underline; }
.ft-waits {
    font-size: 11px; color: var(--color-info); text-decoration: none; flex: none;
}
.ft-waits:hover { text-decoration: underline; }
.ft-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.ft-note { margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-fg-muted); }
.ft-fail {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
.ft-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.ft-link {
    display: flex; align-items: center; gap: 0.5rem; min-width: 10rem; flex: 1 1 0;
    text-decoration: none; color: var(--color-fg);
}
.ft-link:focus-visible { outline: 2px solid var(--color-blue-500); }
.ft-name {
    font-size: 0.8125rem; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
}
.ft-runs { font-size: 11px; color: var(--color-fg-subtle); flex: none; }
</style>
