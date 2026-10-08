<script setup>
// A labelled run of results: a kind's group, a Drill's page, or one of an
// address's "resolves to" / "· text" groups. The header's count is whatever
// the caller says it is — `group.total`, or `…` / `!` while that is unknown.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import SearchListAction from './SearchListAction.vue'
import SearchResultRow from './SearchResultRow.vue'
import { KIND_META } from '../../utils/searchDocs.js'

const props = defineProps({
  kind: { type: String, default: null },
  label: { type: String, required: true },
  // No header at all: the Drill draws its own above the rows.
  headless: { type: Boolean, default: false },
  count: { type: String, default: '' },
  aside: { type: String, default: '' },
  rows: { type: Array, default: () => [] },
  more: { type: Object, default: null },
  pending: { type: Boolean, default: false },
  retryText: { type: String, default: '' },
  activeId: { type: String, default: null },
  query: { type: String, default: '' },
  peak: { type: Number, default: 1 },
})
defineEmits(['select', 'open', 'activate', 'retry'])

const meta = computed(() => KIND_META[props.kind] || {
  icon: 'list', accent: 'var(--color-fg-subtle)',
})

const SHIMMER_ROWS = 3
</script>

<template>
  <section role="group" :aria-label="label" :data-kind="kind || undefined">
    <header v-if="!headless" class="group-head">
      <Icon class="group-glyph" :name="meta.icon" :size="12" :style="{ color: meta.accent }" />
      <span class="group-label">{{ label }}</span>
      <span class="group-count" :style="{ '--accent': meta.accent }">{{ count }}</span>
      <span class="group-rule" />
      <span v-if="aside" class="group-aside">{{ aside }}</span>
      <Button
        v-if="retryText"
        variant="link"
        size="sm"
        class="group-retry"
        @click="$emit('retry', kind)"
      >{{ retryText }}</Button>
    </header>

    <div v-if="pending" class="shimmer-wrap">
      <div v-for="n in SHIMMER_ROWS" :key="n" class="shimmer" />
    </div>

    <SearchResultRow
      v-for="row in rows"
      :key="row.key"
      :entry="row"
      :query="query"
      :active="row.key === activeId"
      :peak="peak"
      @select="$emit('select', $event)"
      @open="$emit('open', $event)"
    />

    <SearchListAction
      v-if="more"
      :entry="more"
      :active="more.key === activeId"
      @activate="$emit('activate', $event)"
    />
    <slot />
  </section>
</template>

<style scoped>
.group-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.875rem 0.75rem 0.375rem;
}

.group-label {
  font-size: var(--sp-t-micro);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-fg-subtle);
  font-weight: 700;
  white-space: nowrap;
  margin: 0;
}

.group-glyph { flex-shrink: 0; opacity: 0.9; }

.group-count {
  font-size: var(--sp-t-micro);
  font-weight: 700;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-radius: 6px;
  padding: 1px 6px;
}

.group-rule { flex: 1; height: 1px; background: var(--color-border); }

.group-aside { font-size: var(--sp-t-micro); color: var(--color-fg-faint); white-space: nowrap; }

.group-retry {
  font-size: var(--sp-t-micro);
  color: var(--color-danger);
  white-space: nowrap;
}

.shimmer-wrap { padding: 0.25rem 0.75rem; display: grid; gap: 0.5rem; }

.shimmer {
  height: 2.25rem;
  border-radius: 11px;
  background: linear-gradient(90deg,
    var(--color-surface-2) 25%, var(--color-background) 50%, var(--color-surface-2) 75%);
  background-size: 200% 100%;
  animation: search-shimmer 1.2s ease-in-out infinite;
}

@keyframes search-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .shimmer { animation: none; }
}
</style>
