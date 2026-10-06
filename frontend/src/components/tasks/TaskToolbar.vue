<script setup>
// The Tasks tab's one toolbar, the same for Board, List and Graph: search,
// the active filters as removable chips, one Filter button, then the view's
// own reading (count, grouping, legend) and the view switch.
//
// Stateless apart from the search draft: Enter commits it, Escape puts the
// box back to what the URL says, and neither makes a request on its own. On
// a phone the box folds behind a "Search" button and the view switch is not
// offered — below 768 px the tab is always the grouped list.
import { nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Tabs from '../ui/Tabs.vue'

const props = defineProps({
  view: { type: String, required: true },
  mobile: { type: Boolean, default: false },
  q: { type: String, default: '' },
  placeholder: { type: String, default: 'Search tasks' },
  //: [{ key, field, value, patch }] — `patch` is what removing it writes.
  chips: { type: Array, default: () => [] },
})
const emit = defineEmits(['patch', 'view', 'search'])

const VIEWS = [
  { value: 'board', label: 'Board', icon: 'board-columns' },
  { value: 'list', label: 'List', icon: 'list' },
  { value: 'graph', label: 'Graph', icon: 'dependency-tree' },
]

const draft = ref(props.q)
const route = useRoute()
watch([() => props.q, () => route.fullPath], () => { draft.value = props.q })
const input = ref(null)
const searching = ref(false)

function commit() {
  const term = draft.value.trim()
  emit('search', term)
}
function revert() {
  draft.value = props.q
  if (props.mobile && !props.q) searching.value = false
}

async function focusSearch() {
  searching.value = true
  await nextTick()
  input.value?.focus()
}

defineExpose({ focusSearch })
</script>

<template>
  <div class="ttb" :class="{ 'is-mobile': mobile }" data-testid="task-toolbar">
    <div class="ttb-filters">
      <Button
        v-if="mobile && !searching && !q"
        variant="secondary"
        class="ttb-pill"
        data-pl-part="key"
        data-testid="task-search-open"
        @click="focusSearch"
      >
        <Icon name="search" :size="14" />
        <span>Search</span>
      </Button>
      <div v-else class="ttb-search" role="search">
        <Icon name="search" :size="14" class="ttb-search-icon" aria-hidden="true" />
        <!-- `text`, not `search`: the native clear widget empties the box on
             Escape without committing, so box and list would disagree. -->
        <Input
          ref="input"
          v-model="draft"
          type="text"
          class="ttb-input"
          data-pl-part="input"
          :placeholder="placeholder"
          :aria-label="placeholder"
          data-testid="list-search"
          @keydown.enter.prevent="commit"
          @keydown.esc.prevent="revert"
        />
        <kbd v-if="!mobile" class="ttb-kbd" data-pl-part="kbd" aria-hidden="true">/</kbd>
      </div>

      <span
        v-for="chip in chips"
        :key="chip.key"
        class="ttb-chip"
        data-pl-part="tag"
        :data-testid="`task-chip-${chip.key}`"
      >
        <span v-if="chip.field" class="ttb-chip-field">{{ chip.field }}</span>
        <span class="ttb-chip-value">{{ chip.value }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="ttb-chip-x"
          :aria-label="`Remove filter ${chip.field || ''} ${chip.value}`.replace(/\s+/g, ' ')"
          :data-testid="`task-chip-remove-${chip.key}`"
          @click="emit('patch', chip.patch)"
        >
          <Icon name="x" :size="12" />
        </Button>
      </span>

      <slot name="filter" />
    </div>

    <div class="ttb-meta">
      <slot name="meta" />
      <Tabs
        v-if="!mobile"
        :model-value="view"
        :tabs="VIEWS"
        aria-label="View"
        data-pl-part="view-switch"
        data-testid="list-views"
        @update:model-value="v => v !== view && emit('view', v)"
      />
    </div>
  </div>
</template>

<style scoped>
.ttb {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.625rem 1rem;
}
.ttb-filters,
.ttb-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
}
.ttb-meta { margin-left: auto; }
.ttb-search {
    position: relative;
    display: flex;
    align-items: center;
    width: 18.75rem;
    max-width: 100%;
}
.ttb-search-icon {
    position: absolute;
    left: 0.625rem;
    color: var(--color-fg-subtle);
    pointer-events: none;
}
.ttb-search :deep(.ttb-input) {
    width: 100%;
    height: 2.125rem;
    padding-left: 2rem;
    padding-right: 2rem;
}
.ttb-kbd {
    position: absolute;
    right: 0.5rem;
    padding: 0 0.3125rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    pointer-events: none;
}
.ttb-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3125rem;
    height: 2.125rem;
    padding: 0 0.25rem 0 0.625rem;
    font-size: 0.8125rem;
    background: var(--color-surface-2);
    border-radius: var(--radius-md);
}
.ttb-chip-field { color: var(--color-fg-muted); }
.ttb-chip-value {
    font-weight: 600;
    color: var(--color-primary);
}
.ttb .ttb-chip-x {
    width: 1.5rem;
    height: 1.5rem;
}
.ttb .ttb-pill { height: 2.75rem; }
.is-mobile .ttb-search {
    flex: 1 1 100%;
    width: auto;
    order: 10;
}
.is-mobile .ttb-search :deep(.ttb-input) { height: 2.75rem; }
.is-mobile .ttb-chip { height: 2.75rem; }
.ttb.is-mobile .ttb-chip-x { width: 2.75rem; height: 2.75rem; }
.is-mobile .ttb-meta { margin-left: 0; }
</style>
