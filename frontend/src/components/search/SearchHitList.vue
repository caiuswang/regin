<script setup>
// Every line of the document the query occurs on, as a concordance.
//
// The reading view answers "what does this hit say"; with eighty-eight of
// them it cannot answer "which hit do I want" — that needs all of them at
// once, which is what this is. Picking a line hands the index back so the
// reading view can reopen parked on it.
import SearchMark from './SearchMark.vue'

defineProps({
  hits: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  capped: { type: Boolean, default: false },
})
defineEmits(['pick'])
</script>

<template>
  <div class="hit-list">
    <div
      v-for="(hit, i) in hits"
      :key="hit.line"
      class="hit-row cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :class="{ 'is-active': i === activeIndex }"
      role="button"
      tabindex="0"
      @click="$emit('pick', i)"
      @keydown.enter.prevent="$emit('pick', i)"
      @keydown.space.prevent="$emit('pick', i)"
    >
      <span class="hit-line">{{ hit.line }}</span>
      <SearchMark class="hit-text" :part="hit" :current="i === activeIndex" />
    </div>

    <p v-if="!hits.length" class="hit-note">
      The term matches this result's title, not its body — there is nothing to list.
    </p>
    <p v-else class="hit-note">
      <template v-if="capped">First {{ hits.length }} lines · </template>
      <kbd>↵</kbd> opens the result at the selected line
    </p>
  </div>
</template>

<style scoped>
.hit-list { padding: 0.375rem 0 0.75rem; }

.hit-row {
  display: grid;
  grid-template-columns: 2.75rem 1fr;
  gap: 0.625rem;
  padding: 0.25rem 1.25rem;
  cursor: pointer;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-body);
  line-height: 1.6;
  transition: background-color 150ms;
}

.hit-row:hover { background: var(--color-surface-2); }

.hit-row:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 2px var(--color-ring);
}

.hit-row.is-active {
  background: var(--color-surface-selected);
  box-shadow: inset 2.5px 0 0 var(--color-primary);
}

.hit-line {
  text-align: right;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
  font-size: var(--sp-t-micro);
  user-select: none;
}

.hit-text {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--color-fg-muted);
}

.hit-row.is-active .hit-text { color: var(--color-fg); }

.hit-note {
  margin: 0.5rem 0 0;
  padding: 0 1.25rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-faint);
  display: flex;
  align-items: center;
  gap: 0.3125rem;
}

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
