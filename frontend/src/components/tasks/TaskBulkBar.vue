<script setup>
// The bar that appears once rows are selected.
//
// Every action is one request naming the ids, and partial success is the
// normal outcome — a selection of forty ideas and two running tasks archives
// the forty. The caller reports what the server changed, never what was
// selected.
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Select from '../ui/Select.vue'
import { PRIORITIES } from '../../utils/taskMeta.js'

defineProps({
  count: { type: Number, default: 0 },
  projects: { type: Array, default: () => [] },
})
const emit = defineEmits(['clear', 'archive', 'restore', 'priority', 'project',
                          'delete'])

const priorityOptions = [
  { value: '', label: 'Set priority…' },
  ...PRIORITIES.map(p => ({ value: p.value, label: p.label })),
]

function apply(event, value) {
  if (!value) return
  emit(event, value)
}
</script>

<template>
  <div class="tb" role="region" aria-label="Bulk actions" data-testid="task-bulk">
    <span class="tb-count">{{ count }} selected</span>

    <Select
      model-value=""
      :options="priorityOptions"
      aria-label="Set priority on the selection"
      data-testid="task-bulk-priority"
      @update:model-value="v => apply('priority', v)"
    />
    <Select
      model-value=""
      :options="[{ value: '', label: 'Move to project…' },
                 { value: 'none', label: 'No project' },
                 ...projects.map(p => ({ value: String(p.id),
                                         label: p.name }))]"
      aria-label="Move the selection to a project"
      data-testid="task-bulk-project"
      @update:model-value="v => apply('project', v)"
    />

    <Button
      variant="ghost"
      size="sm"
      data-testid="task-bulk-archive"
      @click="emit('archive')"
    >
      <Icon name="archive" :size="13" />
      <span>Archive</span>
    </Button>
    <Button
      variant="ghost"
      size="sm"
      data-testid="task-bulk-restore"
      @click="emit('restore')"
    >
      <Icon name="refresh-cw" :size="13" />
      <span>Restore</span>
    </Button>

    <Button
      variant="ghost"
      size="sm"
      class="tb-danger"
      data-testid="task-bulk-delete"
      @click="emit('delete')"
    >
      <Icon name="trash" :size="13" />
      <span>Delete</span>
    </Button>

    <Button
      variant="ghost"
      size="sm"
      aria-label="Clear the selection"
      data-testid="task-bulk-clear"
      @click="emit('clear')"
    >
      <Icon name="x" :size="13" />
      <span>Clear</span>
    </Button>
  </div>
</template>

<style scoped>
.tb {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-primary);
    border-radius: var(--radius-lg);
    background: var(--color-info-soft);
}
.tb-danger { color: var(--color-danger); }
.tb-count {
    font-size: 0.8125rem;
    font-weight: 600;
    color: var(--color-fg);
    font-variant-numeric: tabular-nums;
    margin-right: 0.25rem;
}
</style>
