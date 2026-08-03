<script setup>
// Every narrowing facet in one popover, so the resting toolbar is a single
// row. Reka's PopoverContent owns click-outside, Escape and re-positioning on
// scroll, which a hand-rolled absolutely-positioned panel would have to
// re-solve.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Popover from '../ui/Popover.vue'
import SessionFilterChips from './SessionFilterChips.vue'

const props = defineProps({
  kindOptions: { type: Array, required: true },
  statusOptions: { type: Array, required: true },
  tagOptions: { type: Array, default: () => [] },
  repoOptions: { type: Array, default: () => [] },
  repoCounts: { type: Object, default: () => ({}) },
  activeCount: { type: Number, default: 0 },
})

const emit = defineEmits(['reset', 'commit-trace-id'])

const kind = defineModel('kind', { type: String, default: 'real' })
const status = defineModel('status', { type: String, default: 'all' })
const tag = defineModel('tag', { type: String, default: '' })
const repo = defineModel('repo', { type: String, default: 'all' })
const traceId = defineModel('traceId', { type: String, default: '' })

const open = ref(false)

// Every registered repo is offerable, but `repo_counts` only mentions the ones
// present in the current filter set — so an absent repo means 0, not unknown.
// Stating that 0 (rather than omitting the count) keeps a chip from looking
// like a promising filter that returns an empty list; sorting by count floats
// the repos actually worth clicking above the fold of "+N more".
const repoChips = computed(() => props.repoOptions
  .map(name => ({ value: name, label: name, count: props.repoCounts[name] ?? 0 }))
  .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)))

// The tag facet arrives with a leading "All" entry and separator markers for
// the Select it used to drive; the chip row supplies its own All chip.
const tagChips = computed(() => props.tagOptions
  .filter(o => !o.separator && o.value !== '')
  .map(o => ({ value: o.value, label: o.label, count: o.count ?? 0 }))
  .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)))
</script>

<template>
  <Popover v-model:open="open" align="end">
    <template #trigger>
      <Button class="filters-trigger" :class="{ 'filters-trigger--on': activeCount > 0 }">
        Filters
        <span v-if="activeCount" class="filters-trigger__count">{{ activeCount }}</span>
        <Icon name="chevron-down" :size="12" class="filters-trigger__chevron" :class="{ 'filters-trigger__chevron--open': open }" />
      </Button>
    </template>

    <div class="filters-panel">
      <SessionFilterChips
        v-model="kind"
        label="Kind"
        all-value="real"
        all-label="Real only"
        :options="kindOptions.filter(o => o.value !== 'real')"
        :visible="4"
      />
      <SessionFilterChips
        v-model="status"
        label="Status"
        all-value="all"
        all-label="Any"
        :options="statusOptions.filter(o => o.value !== 'all')"
        :visible="4"
      />
      <SessionFilterChips
        v-model="repo"
        label="Repo"
        all-value="all"
        all-label="All"
        :options="repoChips"
      />
      <SessionFilterChips
        v-model="tag"
        label="Tag"
        all-value=""
        all-label="All"
        :options="tagChips"
      />

      <div class="facet">
        <label class="facet__label" for="filters-trace-id">Trace ID</label>
        <Input
          id="filters-trace-id"
          v-model="traceId"
          type="search"
          class="trace-input focus-visible:outline-2 focus-visible:outline-blue-500"
          placeholder="prefix…"
          title="Case-insensitive prefix match on trace_id (press Enter to apply)"
          @keyup.enter="emit('commit-trace-id')"
        />
      </div>

      <div class="filters-panel__foot">
        <Button variant="ghost" size="sm" @click="emit('reset')">Reset all</Button>
      </div>
    </div>
  </Popover>
</template>

<style scoped>
.filters-trigger {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  font-size: 0.78125rem;
  font-weight: 600;
  gap: 0.375rem;
  height: 2.125rem;
}
.filters-trigger:hover { border-color: var(--color-border-strong); color: var(--color-fg); }
.filters-trigger--on {
  background: var(--color-blue-50);
  border-color: var(--color-primary);
  color: var(--color-blue-800);
}
.filters-trigger__chevron { transition: transform 0.15s ease; }
.filters-trigger__chevron--open { transform: rotate(180deg); }
.filters-trigger__count {
  background: var(--color-primary);
  border-radius: 9999px;
  color: var(--color-primary-fg);
  font-size: 0.625rem;
  line-height: 1;
  min-width: 1rem;
  padding: 0.1875rem 0.3125rem;
  text-align: center;
}

.filters-panel { display: flex; flex-direction: column; gap: 0.75rem; }
.facet {
  align-items: center;
  display: grid;
  gap: 0.5rem;
  grid-template-columns: 3.75rem 1fr;
}
.facet__label {
  color: var(--color-fg-faint);
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.trace-input {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  color: var(--color-fg);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  padding: 0.375rem 0.625rem;
  width: 100%;
}
.trace-input::placeholder { color: var(--color-fg-faint); font-family: inherit; }
.filters-panel__foot {
  border-top: 1px solid var(--color-border-subtle);
  display: flex;
  justify-content: flex-end;
  margin-top: 0.125rem;
  padding-top: 0.625rem;
}
</style>

<!-- NOT scoped: Reka portals the panel to <body>, out of reach of data-v. The
     shared .ds-popover caps width at 20rem, which is too narrow for four chip
     rows; widen only when this panel is the content. -->
<style>
.ds-popover:has(.filters-panel) { max-width: min(28rem, calc(100vw - 2rem)); width: 26rem; }
</style>
