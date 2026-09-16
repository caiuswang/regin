<script setup>
import { ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { DEPTH_MAX, DEPTH_MIN } from '../../composables/useTaskGraph.js'

const props = defineProps({
  root: { type: Number, default: null },
  depth: { type: Number, default: 2 },
  status: { type: String, default: '' },
})
const emit = defineEmits(['focus', 'update:depth', 'update:status', 'refresh'])

const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'idea', label: 'Idea' },
  { value: 'armed', label: 'Armed' },
  { value: 'queued', label: 'Queued' },
  { value: 'running', label: 'Running' },
  { value: 'done', label: 'Done' },
  { value: 'failed', label: 'Failed' },
  { value: 'archived', label: 'Archived' },
]

const draft = ref(props.root == null ? '' : String(props.root))
watch(() => props.root, id => { draft.value = id == null ? '' : String(id) })

// Applied on commit (Enter / blur), not per keystroke: a fetch per digit
// would be a request storm, and debouncing it is a timer.
function applyRoot() {
  const id = parseInt(draft.value, 10)
  emit('focus', Number.isInteger(id) ? id : null)
}

function stepDepth(delta) {
  emit('update:depth', Math.min(DEPTH_MAX, Math.max(DEPTH_MIN, props.depth + delta)))
}
</script>

<template>
  <div class="tg-controls">
    <div class="tg-field">
      <label class="tg-label" for="task-graph-root">Focus</label>
      <Input
        id="task-graph-root"
        v-model="draft"
        class="tg-root-input"
        type="number"
        placeholder="task id"
        aria-label="Focus the graph on a task id"
        data-testid="task-graph-root"
        @keyup.enter="applyRoot"
        @change="applyRoot"
      />
    </div>

    <div class="tg-field">
      <span id="task-graph-depth-label" class="tg-label">Depth</span>
      <div class="tg-stepper" role="group" aria-labelledby="task-graph-depth-label">
        <Button
          variant="ghost"
          size="sm"
          :disabled="depth <= DEPTH_MIN"
          aria-label="Fewer hops from the focused task"
          data-testid="task-graph-depth-less"
          @click="stepDepth(-1)"
        >
          <Icon name="minus" :size="13" />
        </Button>
        <span class="tg-depth" data-testid="task-graph-depth">{{ depth }}</span>
        <Button
          variant="ghost"
          size="sm"
          :disabled="depth >= DEPTH_MAX"
          aria-label="More hops from the focused task"
          data-testid="task-graph-depth-more"
          @click="stepDepth(1)"
        >
          <Icon name="plus" :size="13" />
        </Button>
      </div>
    </div>

    <Select
      :model-value="status"
      :options="STATUS_OPTIONS"
      aria-label="Filter the graph by status"
      data-testid="task-graph-status"
      @update:model-value="emit('update:status', $event)"
    />

    <Button
      v-if="root != null"
      variant="ghost"
      size="sm"
      data-testid="task-graph-clear"
      @click="emit('focus', null)"
    >
      <Icon name="x" :size="13" />
      Clear focus
    </Button>

    <Button variant="secondary" size="sm" data-testid="task-graph-refresh" @click="emit('refresh')">
      <Icon name="refresh-cw" :size="13" />
      Refresh
    </Button>
  </div>
</template>

<style scoped>
.tg-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
}
.tg-field {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}
.tg-label {
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.tg-root-input {
    width: 6.5rem;
}
.tg-stepper {
    display: inline-flex;
    align-items: center;
    gap: 0.125rem;
    padding: 0.125rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
}
.tg-depth {
    min-width: 1.25rem;
    text-align: center;
    font-size: 0.8125rem;
    font-variant-numeric: tabular-nums;
    color: var(--color-fg);
}
</style>
