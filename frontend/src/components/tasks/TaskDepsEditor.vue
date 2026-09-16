<script setup>
// Pick the tasks that must finish before this one. The candidate list and the
// current edges both come from `/api/tasks/graph`, which is the only read that
// answers "what does this depend on" — and answering both from one fetch keeps
// the editor and the DAG panel from disagreeing.
//
// The whole set is PUT at once because that is the route's contract: an id
// unticked here is an edge dropped from the graph.
import { computed, onMounted, ref } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import { useTaskDeps } from '../../composables/useTaskDeps.js'
import { useTaskGraph } from '../../composables/useTaskGraph.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'saved'])

const { nodes, loading, error, load, dependsOn, titleOf } = useTaskGraph()
const { saving, depError, save } = useTaskDeps()
const picked = ref([])
const filter = ref('')

onMounted(async () => {
  await load()
  picked.value = dependsOn(props.task.id)
})

// A done or archived task can never hold this one back, so it is not offered
// — unless it is already an edge, which the reader must still be able to drop.
const SETTLED = new Set(['done', 'archived'])

const candidates = computed(() => nodes.value.filter(n =>
  n.id !== props.task.id && (picked.value.includes(n.id) || !SETTLED.has(n.status))))

function matches(node, needle) {
  return String(node.id).includes(needle)
    || node.title.toLowerCase().includes(needle)
}

const shown = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  if (!needle) return candidates.value
  return candidates.value.filter(n => picked.value.includes(n.id) || matches(n, needle))
})

function toggle(id, on) {
  picked.value = on
    ? [...picked.value, id]
    : picked.value.filter(x => x !== id)
}

async function submit() {
  if (await save(props.task.id, picked.value)) emit('saved')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Dependencies"
    :description="`${task.title} waits for whatever is ticked below.`"
    @update:open="v => !v && emit('close')"
  >
    <div v-if="loading" class="task-deps-note">reading the task graph…</div>
    <div v-else-if="error" class="live-launch-warn" data-testid="task-deps-error">
      {{ error }}
    </div>
    <div v-else-if="candidates.length === 0" class="task-deps-note" data-testid="task-deps-empty">
      There is no other task to wait for yet.
    </div>
    <div v-else class="task-deps-picker">
      <Input
        v-model="filter"
        type="search"
        placeholder="Filter by id or title"
        aria-label="Filter tasks"
        data-testid="task-deps-filter"
      />
      <div v-if="shown.length === 0" class="task-deps-note" data-testid="task-deps-no-match">
        No task matches “{{ filter.trim() }}”.
      </div>
      <div v-else class="task-deps-list" data-testid="task-deps-list">
        <label
          v-for="node in shown"
          :key="node.id"
          class="task-deps-row"
        >
          <Checkbox
            :model-value="picked.includes(node.id)"
            :data-testid="`task-dep-${node.id}`"
            :aria-label="`Depend on task ${node.id}`"
            @update:model-value="on => toggle(node.id, on)"
          />
          <span class="task-deps-id">#{{ node.id }}</span>
          <span class="task-deps-title">{{ node.title }}</span>
          <span class="task-deps-status">{{ node.status }}</span>
        </label>
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

<style scoped>
.task-deps-note {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.task-deps-picker {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
.task-deps-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    max-height: 22rem;
    overflow-y: auto;
}
.task-deps-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.5rem;
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    min-width: 0;
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
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
}
</style>
