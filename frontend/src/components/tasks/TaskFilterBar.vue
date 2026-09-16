<script setup>
// Every control here narrows the *query*, not the loaded rows — which is why
// the count beside it is the server's and not `items.length`, and why every
// one of them writes into the URL rather than into a ref this component owns.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import TaskMoreFilters from './TaskMoreFilters.vue'
import { GROUPS, PRIORITIES, SORTS, STATUS_FILTERS } from '../../utils/taskMeta.js'

const props = defineProps({
  state: { type: Object, required: true },
  active: { type: Number, default: 0 },
  projects: { type: Array, default: () => [] },
  users: { type: Array, default: () => [] },
  // The shell's `?project=` as this list sees it: '' for every project, the
  // literal 'none', or a repo id.
  project: { type: String, default: '' },
  total: { type: Number, default: null },
})
const emit = defineEmits(['patch', 'reset'])

const searchInput = ref(null)
// The `/` shortcut lives on the page, not here, so the page needs a handle on
// the field it is meant to focus.
defineExpose({ focusSearch: () => searchInput.value?.focus() })

// The typed text, which is not yet the query: `q` is committed on Enter. A
// debounce here would be a clock deciding when a search happened, and — now
// that `q` is a URL axis — one history write per keystroke.
const typed = ref(props.state.q)
watch(() => props.state.q, (q) => { typed.value = q })

const statusOptions = computed(() => STATUS_FILTERS.map(
  s => ({ value: s.key, label: s.key ? s.label : 'Any status' })))

const priorityOptions = computed(() => [
  { value: '', label: 'Any priority' },
  ...PRIORITIES.map(p => ({ value: p.value, label: p.label })),
])

const projectOptions = computed(() => [
  { value: '', label: 'Any project' },
  { value: 'none', label: 'No project' },
  ...props.projects.map(p => ({ value: String(p.id), label: p.name })),
])

const archived = computed(() => props.state.archived === '1')
const runs = computed(() => props.state.runs === '1')
</script>

<template>
  <div class="tf">
    <div class="tf-search">
      <Icon name="search" :size="14" class="tf-search-icon" aria-hidden="true" />
      <Input
        ref="searchInput"
        v-model="typed"
        type="search"
        placeholder="Search titles and details…"
        aria-label="Search tasks"
        data-testid="task-search"
        @keyup.enter="emit('patch', { q: typed.trim() })"
      />
    </div>

    <Select
      :model-value="state.status"
      :options="statusOptions"
      aria-label="Filter by status"
      data-testid="task-filter-status"
      @update:model-value="v => emit('patch', { status: v })"
    />
    <Select
      :model-value="state.priority"
      :options="priorityOptions"
      aria-label="Filter by priority"
      data-testid="task-filter-priority"
      @update:model-value="v => emit('patch', { priority: v })"
    />
    <!-- The project is the Pipeline shell's axis. This control writes the same
         `?project=` the tab strip reads, so the two can never describe
         different populations. -->
    <Select
      :model-value="project"
      :options="projectOptions"
      aria-label="Filter by project"
      data-testid="task-filter-project"
      @update:model-value="v => emit('patch', { project: v })"
    />
    <Select
      :model-value="state.sort"
      :options="SORTS"
      aria-label="Sort"
      data-testid="task-sort"
      @update:model-value="v => emit('patch', { sort: v })"
    />
    <Select
      :model-value="state.group"
      :options="GROUPS"
      aria-label="Group by"
      data-testid="task-group"
      @update:model-value="v => emit('patch', { group: v })"
    />

    <TaskMoreFilters :state="state" :users="users" @patch="p => emit('patch', p)" />

    <Button
      variant="ghost"
      size="sm"
      :aria-pressed="archived"
      :aria-label="archived ? 'Hide archived tasks' : 'Show archived tasks'"
      data-testid="task-archived-toggle"
      @click="emit('patch', { archived: archived ? '' : '1' })"
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
      @click="emit('patch', { runs: runs ? '' : '1' })"
    >
      <Icon name="list" :size="13" />
      <span>{{ runs ? 'Hide runs' : 'Show runs' }}</span>
    </Button>

    <Button
      v-if="active"
      variant="ghost"
      size="sm"
      data-testid="task-filters-reset"
      @click="emit('reset')"
    >
      <Icon name="x" :size="13" />
      Clear {{ active }}
    </Button>

    <!-- The filtered population, not the loaded page: every control above is a
         query parameter, so this is what they describe. -->
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
