<script setup>
// Draw-io style edge creation: pick the task at the other end of a new edge.
// Direction decides whether `task` waits for the picked tasks, or the picked
// tasks wait for `task`. Existing edges in that direction are shown checked so
// the same picker can also adjust or remove them.
import { computed, onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import DepKindToggle from './DepKindToggle.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import api from '../../api'
import { useTaskGraph } from '../../composables/useTaskGraph.js'
import { candidatesFor, groupCandidates } from '../../utils/depEditor.js'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  task: { type: Object, required: true },
  direction: { type: String, required: true }, // 'depends-on' | 'blocks'
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'saved'])

const { nodes, edges, loading, error, load } = useTaskGraph()
const picked = ref([])
const filter = ref('')
const saving = ref(false)
const refusal = ref(null)

onMounted(load)

const taskId = computed(() => props.task.id)
const featureId = computed(() => props.task.feature_id
  ?? nodes.value.find(n => n.id === taskId.value)?.feature_id ?? null)
const featureLabel = computed(() => {
  const title = props.task.feature_title || props.task.feature?.title
  return `This feature (#${featureId.value}${title ? ` ${title}` : ''})`
})

const titleText = computed(() =>
  props.direction === 'depends-on'
    ? `Task #${taskId.value} depends on…`
    : `…depends on task #${taskId.value}`)

const descriptionText = computed(() =>
  props.direction === 'depends-on'
    ? 'Pick the tasks that must finish before this one.'
    : 'Pick the tasks that should wait for this one.')

// Existing edges in the chosen direction: depends-on = task's blockers,
// blocks = tasks that list task as a blocker.
const currentEdges = computed(() => {
  if (props.direction === 'depends-on') {
    return edges.value
      .filter(e => e.task_id === taskId.value)
      .map(e => ({ id: e.depends_on_id, kind: e.kind || 'requires' }))
  }
  return edges.value
    .filter(e => e.depends_on_id === taskId.value)
    .map(e => ({ id: e.task_id, kind: e.kind || 'requires' }))
})

watch(currentEdges, (next) => { picked.value = next }, { immediate: true })

const pickedIds = computed(() => new Set(picked.value.map(e => e.id)))

// Exclude tasks that would create an immediate cycle for this direction.
const blockedIds = computed(() => new Set(nodes.value
  .filter(n => {
    if (n.id === taskId.value) return true
    if (props.direction === 'depends-on') {
      // A task that already depends on `task` cannot become its dependency.
      return edges.value.some(e => e.depends_on_id === taskId.value && e.task_id === n.id)
    }
    // A task that `task` already depends on cannot become its dependent.
    return edges.value.some(e => e.task_id === taskId.value && e.depends_on_id === n.id)
  })
  .map(n => n.id)))

const candidates = computed(() => candidatesFor(nodes.value, taskId.value, pickedIds.value)
  .filter(n => !blockedIds.value.has(n.id)))

const groups = computed(() => groupCandidates(candidates.value, {
  featureId: featureId.value,
  featureLabel: featureLabel.value,
  pickedIds: pickedIds.value,
  filter: filter.value,
}))

const pickedCount = computed(() => picked.value.length)

function kindOf(id) {
  return picked.value.find(e => e.id === id)?.kind || 'requires'
}

function setKind(id, kind) {
  picked.value = picked.value.map(e => (e.id === id ? { ...e, kind } : e))
}

function toggle(id, on) {
  if (on === pickedIds.value.has(id)) return
  picked.value = on
    ? [...picked.value, { id, kind: 'requires' }]
    : picked.value.filter(e => e.id !== id)
}

async function serverDeps(id) {
  const data = await api.get(`/tasks/graph?root=${id}&depth=1`)
  return (data.edges || [])
    .filter(e => e.task_id === id)
    .map(e => ({ id: e.depends_on_id, kind: e.kind || 'requires' }))
}

async function saveDependsOn() {
  const result = await api.put(`/tasks/${taskId.value}/deps`, { depends_on: picked.value })
  if (result.ok === false) {
    refusal.value = { message: result.msg || 'The server refused these dependencies.', pair: result.body?.pair || [] }
    return false
  }
  return true
}

async function saveBlocks() {
  // For each picked dependent, merge task into its existing dependencies.
  for (const e of picked.value) {
    const merged = new Map((await serverDeps(e.id)).map(d => [d.id, d.kind]))
    merged.set(taskId.value, e.kind)
    const result = await api.put(`/tasks/${e.id}/deps`, {
      depends_on: [...merged].map(([id, kind]) => ({ id, kind })),
    })
    if (result.ok === false) {
      refusal.value = { message: result.msg || `The server refused the change for task ${e.id}.`, pair: result.body?.pair || [] }
      return false
    }
  }
  return true
}

async function submit() {
  saving.value = true
  refusal.value = null
  try {
    const ok = props.direction === 'depends-on' ? await saveDependsOn() : await saveBlocks()
    if (ok) emit('saved')
  } catch (e) {
    refusal.value = { message: e?.message || String(e), pair: [] }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    :title="titleText"
    class="task-edge-picker"
    :description="descriptionText"
    @update:open="v => !v && emit('close')"
  >
    <div v-if="loading" class="tep-note">reading the task graph…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-edge-picker-error">
      {{ error }}
    </div>
    <div v-else-if="candidates.length === 0" class="tep-note" data-testid="task-edge-picker-empty">
      There is no other task to connect to yet.
    </div>
    <div v-else class="tep-body">
      <div class="tep-head">
        <Input
          v-model="filter"
          type="search"
          placeholder="Filter by id or title"
          aria-label="Filter tasks"
          data-testid="task-edge-picker-filter"
        />
        <span class="tep-count" data-pl-part="meta-label">{{ pickedCount }} selected</span>
      </div>
      <div v-if="groups.length === 0" class="tep-note" data-testid="task-edge-picker-no-match">
        No task matches “{{ filter.trim() }}”.
      </div>
      <div v-else class="tep-list" data-testid="task-edge-picker-list">
        <section
          v-for="group in groups"
          :key="group.key"
          class="tep-group"
          :data-testid="`task-edge-picker-group-${group.key}`"
        >
          <h3 class="tep-group-head">
            <span>{{ group.label }}</span>
            <span class="tep-group-count">{{ group.rows.length }}</span>
          </h3>
          <div
            v-for="node in group.rows"
            :key="node.id"
            class="tep-row"
            :class="{ 'is-picked': pickedIds.has(node.id) }"
            :data-testid="`task-edge-picker-row-${node.id}`"
          >
            <label class="tep-pick">
              <Checkbox
                :model-value="pickedIds.has(node.id)"
                :data-testid="`task-edge-picker-check-${node.id}`"
                :aria-label="`Connect task ${node.id}`"
                @update:model-value="on => toggle(node.id, on)"
              />
              <TaskStatusIcon :status="node.status" :size="12" />
              <span class="tep-id">#{{ node.id }}</span>
              <span class="tep-title">{{ taskTitle(node) }}</span>
            </label>
            <DepKindToggle
              v-if="pickedIds.has(node.id)"
              :model-value="kindOf(node.id)"
              :label="`What task ${node.id} means`"
              :data-testid="`task-edge-picker-kind-${node.id}`"
              @update:model-value="kind => setKind(node.id, kind)"
            />
            <span v-else class="tep-status">{{ node.status }}</span>
          </div>
        </section>
      </div>
      <p class="tep-hint">
        <strong>needs it</strong> waits for success; <strong>just after</strong> only waits for it to stop.
      </p>
    </div>

    <p v-if="refusal" class="live-launch-warn" role="alert" data-testid="task-edge-picker-refusal">
      {{ refusal.message }}
      <span v-if="refusal.pair.length === 2">
        — offending pair <strong>{{ refusal.pair[0] }} → {{ refusal.pair[1] }}</strong>
      </span>
    </p>

    <template #footer>
      <Button variant="ghost" :disabled="saving" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || loading"
        data-testid="task-edge-picker-save"
        @click="submit"
      >
        {{ saving ? 'Saving…' : 'Save' }}
      </Button>
    </template>
  </Dialog>
</template>

<style>
.ds-dialog.task-edge-picker { width: min(32rem, calc(100vw - 2rem)); }
</style>

<style scoped>
.tep-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tep-body {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
    min-width: 0;
}
.tep-head {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
.tep-head > :first-child { flex: 1; }
.tep-count {
    flex-shrink: 0;
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.tep-list {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    max-height: 22rem;
    padding-right: 0.25rem;
    overflow-y: auto;
}
.tep-group {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}
.tep-group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin: 0;
    padding: 0 0.25rem;
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--color-fg-subtle);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.tep-group-count {
    padding: 0.0625rem 0.375rem;
    font-size: 0.625rem;
    color: var(--color-fg-muted);
    background: var(--color-surface-2);
    border-radius: var(--radius-md);
    font-variant-numeric: tabular-nums;
}
.tep-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.625rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    min-width: 0;
    transition: border-color 120ms ease, background-color 120ms ease;
}
.tep-row:hover { border-color: var(--color-border-strong); }
.tep-row.is-picked {
    background: var(--color-surface-2);
    border-color: color-mix(in srgb, var(--color-primary) 18%, var(--color-border));
    box-shadow: inset 2px 0 0 var(--color-primary);
}
.tep-pick {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1;
    min-width: 0;
    cursor: pointer;
}
.tep-id {
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.tep-title {
    flex: 1;
    min-width: 0;
    font-size: 0.8125rem;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.tep-status {
    flex-shrink: 0;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
}
.tep-hint {
    margin: 0;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
</style>
