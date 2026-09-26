<script setup>
// The one toolbar every Pipeline list renders, in one order: view, search,
// facets, the tab's own controls, sort, Clear, count.
//
// Stateless apart from the search draft: every control emits a patch and the
// view writes it into the URL. The draft is the one exception because an
// uncommitted word is not yet a predicate — Enter commits it, Escape puts the
// box back to what the URL says, and neither makes a request on its own.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import Input from './ui/Input.vue'
import Select from './ui/Select.vue'
import Tabs from './ui/Tabs.vue'

const props = defineProps({
  //: [{ value, label }] — a segmented switcher at the toolbar's left.
  views: { type: Array, default: () => [] },
  view: { type: String, default: '' },
  // Hide search/facets/sort, keeping only the view switcher and the slot.
  bare: { type: Boolean, default: false },
  q: { type: String, default: '' },
  searchLabel: { type: String, default: 'Search' },
  searchPlaceholder: { type: String, default: 'Search…' },
  //: [{ key, label, value, options: [{ value, label }] }]
  facets: { type: Array, default: () => [] },
  sortOptions: { type: Array, default: () => [] },
  sortKey: { type: String, default: '' },
  sortDir: { type: String, default: 'desc' },
  // What the server matched and the unnarrowed population. Either null hides
  // the count slot; the rows still render.
  total: { type: Number, default: null },
  population: { type: Number, default: null },
  noun: { type: String, required: true },
  dirtyCount: { type: Number, default: 0 },
})
const emit = defineEmits(['patch', 'reset', 'view', 'sort-key', 'toggle-dir'])

const draft = ref(props.q)
// Any navigation retires an unsubmitted term: it was typed against rows that
// are no longer the ones below it, and the box must never describe others.
const route = useRoute()
watch([() => props.q, () => route.fullPath], () => { draft.value = props.q })
const search = ref(null)

function commit() {
  const term = draft.value.trim()
  if (term !== props.q) emit('patch', { q: term })
}
function revert() { draft.value = props.q }

function plural(n) { return n === 1 ? props.noun : `${props.noun}s` }

// R4: unnarrowed → "M nouns"; narrowed → "N of M nouns". A widening control
// (archived, feature steps) can make N exceed M, and "12 of 9" is nonsense,
// so that reading states N alone.
const countText = computed(() => {
  const { total, population } = props
  if (total == null || population == null) return ''
  const narrowed = props.dirtyCount > 0 || total !== population
  if (narrowed && total <= population) return `${total} of ${population} ${plural(population)}`
  return `${total} ${plural(total)}`
})

const dirLabel = computed(() => (props.sortDir === 'asc'
  ? 'Sorted ascending — switch to descending'
  : 'Sorted descending — switch to ascending'))

const clearLabel = computed(() => (props.dirtyCount
  ? `Clear ${props.dirtyCount} ${props.dirtyCount === 1 ? 'filter' : 'filters'}`
  : 'Clear filters'))

defineExpose({ focusSearch: () => search.value?.focus() })
</script>

<template>
  <div class="plt" data-testid="pipeline-list-toolbar">
    <Tabs
      v-if="views.length"
      :model-value="view"
      :tabs="views"
      aria-label="View"
      data-testid="list-views"
      @update:model-value="v => v !== view && emit('view', v)"
    />

    <template v-if="!bare">
      <div class="plt-search" role="search">
        <Icon name="search" :size="14" class="plt-search-icon" aria-hidden="true" />
        <!-- `text`, not `search`: the native clear widget answers Escape by
             emptying the box without committing, so the box and the list
             would disagree about what is on screen. -->
        <Input
          ref="search"
          v-model="draft"
          type="text"
          :placeholder="searchPlaceholder"
          :aria-label="searchLabel"
          data-testid="list-search"
          @keydown.enter.prevent="commit"
          @keydown.esc.prevent="revert"
        />
      </div>

      <Select
        v-for="facet in facets"
        :key="facet.key"
        :model-value="facet.value"
        :options="facet.options"
        :aria-label="facet.label"
        :data-testid="`list-facet-${facet.key}`"
        @update:model-value="v => emit('patch', { [facet.key]: v })"
      />
    </template>

    <slot name="controls" />

    <template v-if="!bare">
      <span v-if="sortOptions.length" class="plt-sort">
        <Select
          :model-value="sortKey"
          :options="sortOptions"
          aria-label="Sort by"
          data-testid="list-sort"
          @update:model-value="v => emit('patch', { sort: `${v}:${sortDir}` })"
        />
        <Button
          variant="secondary"
          size="sm"
          :aria-label="dirLabel"
          data-testid="list-sort-dir"
          @click="emit('patch', { sort: `${sortKey}:${sortDir === 'asc' ? 'desc' : 'asc'}` })"
        >
          <Icon :name="sortDir === 'asc' ? 'arrow-up' : 'arrow-down'" :size="12" />
        </Button>
      </span>

      <!-- Rendered and disabled at 0 rather than hidden, so its absence never
           reads as "this tab cannot be reset". -->
      <Button
        variant="ghost"
        size="sm"
        :disabled="!dirtyCount"
        :aria-label="clearLabel"
        data-testid="list-clear"
        @click="emit('reset')"
      >
        <Icon name="x" :size="13" />
        {{ dirtyCount ? `Clear ${dirtyCount}` : 'Clear' }}
      </Button>

      <span v-if="countText" class="plt-count" data-testid="list-count">{{ countText }}</span>
    </template>
  </div>
</template>

<style scoped>
.plt {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
}
.plt-search {
    position: relative;
    flex: 1 1 14rem;
    min-width: 10rem;
    max-width: 24rem;
}
.plt-search :deep(input) { padding-left: 2rem; }
.plt-search-icon {
    position: absolute;
    left: 0.625rem;
    top: 50%;
    transform: translateY(-50%);
    color: var(--color-fg-faint);
    pointer-events: none;
    z-index: 1;
}
.plt-sort { display: inline-flex; align-items: center; gap: 0.25rem; }
.plt-count {
    margin-left: auto;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
</style>
