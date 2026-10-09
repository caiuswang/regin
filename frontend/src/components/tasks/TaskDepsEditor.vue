<script setup>
// Pick the tasks that must finish before this one. The candidate list, the
// current edges and the preview all come from one `/api/tasks/graph` read,
// which keeps the editor and the DAG panel from disagreeing.
//
// The whole set is PUT at once because that is the route's contract: an id
// unticked here is an edge dropped from the graph.
import { computed, onMounted, ref } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import DepKindToggle from './DepKindToggle.vue'
import TaskDepsPreview from './TaskDepsPreview.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { useTaskDeps } from '../../composables/useTaskDeps.js'
import { useTaskGraph } from '../../composables/useTaskGraph.js'
import { candidatesFor, groupCandidates, previewSubset } from '../../utils/depEditor.js'
import { taskTitle } from '../../utils/taskTitle.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'saved'])

const { nodes, edges, loading, error, load, depEdgesOf, titleOf } = useTaskGraph()
const { saving, depError, save } = useTaskDeps()
const picked = ref([])
const filter = ref('')

onMounted(async () => {
  await load()
  picked.value = depEdgesOf(props.task.id)
})

const pickedIds = computed(() => new Set(picked.value.map(e => e.id)))

const featureId = computed(() => props.task.feature_id
  ?? nodes.value.find(n => n.id === props.task.id)?.feature_id ?? null)

const featureLabel = computed(() => {
  const title = props.task.feature_title || props.task.feature?.title
  return `This feature (#${featureId.value}${title ? ` ${title}` : ''})`
})

const candidates = computed(() => candidatesFor(nodes.value, props.task.id, pickedIds.value))

const groups = computed(() => groupCandidates(candidates.value, {
  featureId: featureId.value,
  featureLabel: featureLabel.value,
  pickedIds: pickedIds.value,
  filter: filter.value,
}))

const subset = computed(() => previewSubset({
  nodes: nodes.value,
  edges: edges.value,
  taskId: props.task.id,
  picked: picked.value,
  featureId: featureId.value,
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

async function submit() {
  if (await save(props.task.id, picked.value)) emit('saved')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Dependencies"
    class="task-deps-dialog"
    :description="`${taskTitle(task)} waits for the tasks selected below.`"
    @update:open="v => !v && emit('close')"
  >
    <div v-if="loading" class="task-deps-note">reading the task graph…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-deps-error">
      {{ error }}
    </div>
    <div v-else-if="candidates.length === 0" class="task-deps-note" data-testid="task-deps-empty">
      There is no other task to wait for yet.
    </div>
    <div v-else class="task-deps-split">
      <div class="task-deps-picker">
        <div class="task-deps-picker-head">
          <Input
            v-model="filter"
            type="search"
            placeholder="Filter by id or title"
            aria-label="Filter tasks"
            data-testid="task-deps-filter"
          />
          <span class="task-deps-picker-count" data-pl-part="meta-label">
            {{ pickedCount }} selected
          </span>
        </div>
        <div v-if="groups.length === 0" class="task-deps-note" data-testid="task-deps-no-match">
          No task matches “{{ filter.trim() }}”.
        </div>
        <div v-else class="task-deps-list" data-testid="task-deps-list">
          <section
            v-for="group in groups"
            :key="group.key"
            class="task-deps-group"
            :data-testid="`task-deps-group-${group.key}`"
          >
            <h3 class="task-deps-group-head">
              <span>{{ group.label }}</span>
              <span class="task-deps-group-count">{{ group.rows.length }}</span>
            </h3>
            <!-- The kind control sits OUTSIDE the label: a click anywhere
                 inside a label activates its control, so a toggle nested in
                 one would untick the very edge the reader was classifying. -->
            <div
              v-for="node in group.rows"
              :key="node.id"
              class="task-deps-row"
              :class="{ 'is-picked': pickedIds.has(node.id) }"
              :data-testid="`task-deps-row-${node.id}`"
            >
              <label class="task-deps-pick">
                <Checkbox
                  :model-value="pickedIds.has(node.id)"
                  :data-testid="`task-dep-${node.id}`"
                  :aria-label="`Depend on task ${node.id}`"
                  @update:model-value="on => toggle(node.id, on)"
                />
                <TaskStatusIcon :status="node.status" :size="12" />
                <span class="task-deps-id">#{{ node.id }}</span>
                <span class="task-deps-title">{{ taskTitle(node) }}</span>
              </label>
              <DepKindToggle
                v-if="pickedIds.has(node.id)"
                :model-value="kindOf(node.id)"
                :label="`What task ${node.id} means to this one`"
                :data-testid="`task-dep-kind-${node.id}`"
                @update:model-value="kind => setKind(node.id, kind)"
              />
              <span v-else class="task-deps-status">{{ node.status }}</span>
            </div>
          </section>
        </div>
        <p class="task-deps-hint">
          <strong>needs it</strong> waits for success; <strong>just after</strong> only waits for it to stop.
        </p>
      </div>
      <div class="task-deps-preview-wrap">
        <p class="task-deps-preview-title">Preview</p>
        <TaskDepsPreview
          :subset="subset"
          @add="id => toggle(id, true)"
          @remove="id => toggle(id, false)"
        />
      </div>
    </div>

    <p v-if="depError" class="live-launch-warn" role="alert" data-testid="task-deps-refusal">
      {{ depError.message }}
      <span v-if="depError.pair.length === 2">
        — the offending pair is
        <strong>{{ depError.pair[0] }} → {{ depError.pair[1] }}</strong>
        ({{ titleOf(depError.pair[0]) }} → {{ titleOf(depError.pair[1]) }})
      </span>
    </p>

    <template #footer>
      <Button variant="ghost" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || loading"
        data-testid="task-deps-save"
        @click="submit"
      >
        {{ saving ? 'Saving…' : 'Save dependencies' }}
      </Button>
    </template>
  </Dialog>
</template>

<style>
/* The dialog is portalled out of this component, so its width cannot be
   reached from the scoped block below. */
.ds-dialog.task-deps-dialog { width: min(54rem, calc(100vw - 2rem)); }
</style>

<style scoped>
.task-deps-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.task-deps-split {
    display: grid;
    grid-template-columns: minmax(0, 0.45fr) minmax(0, 0.55fr);
    gap: 1.25rem;
    align-items: start;
}
@media (max-width: 720px) {
    .task-deps-split { grid-template-columns: minmax(0, 1fr); }
}
.task-deps-picker {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
    min-width: 0;
}
.task-deps-picker-head {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
.task-deps-picker-head > :first-child { flex: 1; }
.task-deps-picker-count {
    flex-shrink: 0;
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.task-deps-list {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    max-height: 26rem;
    padding-right: 0.25rem;
    overflow-y: auto;
}
.task-deps-group {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
}
.task-deps-group-head {
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
.task-deps-group-count {
    padding: 0.0625rem 0.375rem;
    font-size: 0.625rem;
    color: var(--color-fg-muted);
    background: var(--color-surface-2);
    border-radius: var(--radius-md);
    font-variant-numeric: tabular-nums;
}
.task-deps-row {
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
.task-deps-row:hover { border-color: var(--color-border-strong); }
.task-deps-row.is-picked {
    background: var(--color-surface-2);
    border-color: color-mix(in srgb, var(--color-primary) 18%, var(--color-border));
    box-shadow: inset 2px 0 0 var(--color-primary);
}
.task-deps-pick {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1;
    min-width: 0;
    cursor: pointer;
}
.task-deps-id {
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.task-deps-title {
    flex: 1;
    min-width: 0;
    font-size: 0.8125rem;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.task-deps-status {
    flex-shrink: 0;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
}
.task-deps-hint {
    margin: 0;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.task-deps-preview-wrap {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    min-width: 0;
    padding: 0.75rem;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.task-deps-preview-title {
    margin: 0;
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--color-fg-subtle);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
</style>
