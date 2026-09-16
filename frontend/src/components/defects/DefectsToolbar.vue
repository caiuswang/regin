<script setup>
// The controls above the list: scope, status, search, sort, reset — in that
// order on every Pipeline tab.
//
// Every one of them is a write to the URL and a read from it; none of them
// holds a copy of the list's state. The search box is the single exception,
// and only while it is being typed into: an uncommitted word is not yet a
// predicate, so it is not yet a fact about the rows on screen. Enter commits
// it, Escape abandons it, and both put the box back in step with the URL.
import { ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import Tabs from '../ui/Tabs.vue'
import { SCOPES, STATUSES, SORT_KEYS } from '../../composables/useDefectsQuery'

const props = defineProps({
  state: { type: Object, required: true },
  sortKey: { type: String, required: true },
  sortDir: { type: String, required: true },
  narrowed: { type: Boolean, default: false },
  // A followed key is a scope you cannot leave with a pill, so the pills are
  // hidden rather than rendered pointing somewhere else.
  showScopes: { type: Boolean, default: true },
})
const emit = defineEmits(['patch', 'reset'])

const draft = ref(props.state.q)
watch(() => props.state.q, (q) => { draft.value = q })

const scope = ref(props.state.scope)
watch(() => props.state.scope, (s) => { scope.value = s })
watch(scope, (s) => { if (s !== props.state.scope) emit('patch', { scope: s }) })

function commitSearch() {
  emit('patch', { q: draft.value.trim() })
}

function abandonSearch() {
  draft.value = props.state.q
}

function toggleDir() {
  emit('patch', { sort: `${props.sortKey}:${props.sortDir === 'asc' ? 'desc' : 'asc'}` })
}
</script>

<template>
  <div class="toolbar" data-testid="defects-toolbar">
    <Tabs
      v-if="showScopes"
      v-model="scope"
      :tabs="SCOPES"
      aria-label="Defect scope"
      data-testid="defects-scope"
    />

    <form class="search" role="search" @submit.prevent="commitSearch">
      <label class="sr-only" for="defects-q">Search defects by symptom</label>
      <!-- `text`, not `search`: Chromium's native clear widget answers
           Escape by emptying the box without committing anything, which is
           the box and the list disagreeing about what is on screen. Escape
           here puts the draft back to what the URL says. -->
      <Input
        id="defects-q"
        v-model="draft"
        type="text"
        placeholder="Search symptoms…"
        data-testid="defects-search"
        @keydown.esc="abandonSearch"
      />
      <Button type="submit" variant="secondary" size="sm">Search</Button>
    </form>

    <label class="field">
      <span class="sr-only">Status</span>
      <Select
        :model-value="state.status"
        :options="STATUSES"
        aria-label="Status"
        data-testid="defects-status"
        @update:model-value="v => emit('patch', { status: v })"
      />
    </label>

    <label class="field">
      <span class="sr-only">Sort by</span>
      <Select
        :model-value="sortKey"
        :options="SORT_KEYS"
        aria-label="Sort by"
        data-testid="defects-sort"
        @update:model-value="v => emit('patch', { sort: `${v}:${sortDir}` })"
      />
    </label>

    <Button
      variant="secondary"
      size="sm"
      :aria-label="`Sort ${sortDir === 'asc' ? 'ascending' : 'descending'}, change to ${sortDir === 'asc' ? 'descending' : 'ascending'}`"
      data-testid="defects-sort-dir"
      @click="toggleDir"
    >{{ sortDir === 'asc' ? 'Oldest first' : 'Newest first' }}
    </Button>

    <Button
      v-if="narrowed"
      variant="ghost"
      size="sm"
      data-testid="defects-reset"
      @click="emit('reset')"
    >Reset</Button>
  </div>
</template>

<style scoped>
.toolbar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
}
.search { display: flex; align-items: center; gap: 0.35rem; }
.search :deep(.input) { min-width: 14rem; }
.field { display: inline-flex; align-items: center; }
</style>
