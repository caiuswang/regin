<script setup>
// Every control here narrows the *query*, not the loaded rows — which is why
// the count beside it is the server's and not `items.length`.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { GROUPS, PRIORITIES, SORTS, STATUS_FILTERS } from '../../utils/taskMeta.js'

const props = defineProps({
  filters: { type: Object, required: true },
  projects: { type: Array, default: () => [] },
  total: { type: Number, default: null },
})
defineEmits(['submit-search'])

const searchInput = ref(null)
// The `/` shortcut lives on the page, not here, so the page needs a handle on
// the field it is meant to focus.
defineExpose({ focusSearch: () => searchInput.value?.focus() })

const statusOptions = computed(() => STATUS_FILTERS.map(
  s => ({ value: s.key, label: s.key ? s.label : 'Any status' })))

const priorityOptions = computed(() => [
  { value: '', label: 'Any priority' },
  ...PRIORITIES.map(p => ({ value: p.value, label: p.label })),
])

const projectOptions = computed(() => [
  { value: '', label: 'Any project' },
  { value: 'none', label: 'No project' },
  ...props.projects.map(p => ({ value: String(p.id),
                                label: p.name })),
])

const archived = computed({
  get: () => props.filters.includeArchived.value === '1',
  set: (on) => { props.filters.includeArchived.value = on ? '1' : '' },
})

const runs = computed({
  get: () => props.filters.showRuns.value === '1',
  set: (on) => { props.filters.showRuns.value = on ? '1' : '' },
})
</script>

<template>
  <div class="tf">
    <div class="tf-search">
      <Icon name="search" :size="14" class="tf-search-icon" aria-hidden="true" />
      <Input
        ref="searchInput"
        v-model="filters.search.value"
        type="search"
        placeholder="Search titles and details…"
        aria-label="Search tasks"
        data-testid="task-search"
        @keyup.enter="$emit('submit-search')"
      />
    </div>

    <Select
      v-model="filters.status.value"
      :options="statusOptions"
      aria-label="Filter by status"
      data-testid="task-filter-status"
    />
    <Select
      v-model="filters.priority.value"
      :options="priorityOptions"
      aria-label="Filter by priority"
      data-testid="task-filter-priority"
    />
    <Select
      v-model="filters.projectId.value"
      :options="projectOptions"
      aria-label="Filter by project"
      data-testid="task-filter-project"
    />
    <Select
      v-model="filters.sort.value"
      :options="SORTS"
      aria-label="Sort"
      data-testid="task-sort"
    />
    <Select
      v-model="filters.groupBy.value"
      :options="GROUPS"
      aria-label="Group by"
      data-testid="task-group"
    />

    <Button
      variant="ghost"
      size="sm"
      :aria-pressed="archived"
      :aria-label="archived ? 'Hide archived tasks' : 'Show archived tasks'"
      data-testid="task-archived-toggle"
      @click="archived = !archived"
    >
      <Icon name="archive" :size="13" />
      <span>{{ archived ? 'Hide archived' : 'Show archived' }}</span>
    </Button>

    <Button
      variant="ghost"
      size="sm"
      :aria-pressed="runs"
      :aria-label="runs ? 'List features only' : 'List runs beside features'"
      data-testid="task-runs-toggle"
      @click="runs = !runs"
    >
      <Icon name="list" :size="13" />
      <span>{{ runs ? 'Hide runs' : 'Show runs' }}</span>
    </Button>

    <Button
      v-if="filters.active.value"
      variant="ghost"
      size="sm"
      data-testid="task-filters-reset"
      @click="filters.reset()"
    >
      <Icon name="x" :size="13" />
      Clear {{ filters.active.value }}
    </Button>

    <span v-if="total != null" class="tf-total" data-testid="task-total">
      {{ total }} {{ total === 1 ? 'task' : 'tasks' }}
    </span>
  </div>
</template>

<style scoped>
.tf {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
}
.tf-search {
    position: relative;
    flex: 1 1 14rem;
    min-width: 10rem;
}
.tf-search :deep(input) { padding-left: 2rem; }
.tf-search-icon {
    position: absolute;
    left: 0.625rem;
    top: 50%;
    transform: translateY(-50%);
    color: var(--color-fg-faint);
    pointer-events: none;
    z-index: 1;
}
.tf-total {
    margin-left: auto;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
</style>
