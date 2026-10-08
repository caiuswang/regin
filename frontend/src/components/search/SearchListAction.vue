<script setup>
// The rows that are not results but are reached like them (R2): a group's
// "Show all N", the Drill's next page, and that page's retry. Each is an
// option in the listbox, so ↓ lands on it and ↵ does it — overflow is never
// a dead end the keyboard cannot cross.
import { computed } from 'vue'
import { DRILL_PAGE, kindNoun, rowDomId } from '../../utils/searchDocs.js'

const props = defineProps({
  entry: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
defineEmits(['activate'])

const label = computed(() => {
  const e = props.entry
  if (e.type === 'more') return `Show all ${e.total} ${kindNoun(e.kind, e.total)} →`
  if (e.type === 'retry') return "Couldn't load the next page · retry"
  if (e.loading) return 'Loading…'
  return `Load ${Math.min(DRILL_PAGE, e.left)} more · ${e.left} left`
})
</script>

<template>
  <div
    :id="rowDomId(entry.key)"
    role="option"
    :aria-selected="active"
    class="list-action cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    :class="[`is-${entry.type}`, { 'is-active': active }]"
    :data-entry="entry.type"
    @click="$emit('activate', entry)"
  >
    <span class="list-action-label">{{ label }}</span>
    <span v-if="active" class="list-action-enter">↵</span>
  </div>
</template>

<style scoped>
.list-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin: 0.25rem 0 0;
  padding: 0.4375rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  font-size: var(--sp-t-meta);
  font-weight: 600;
  color: var(--color-fg-muted);
}

.list-action:hover { background: var(--color-background); color: var(--color-fg); }

.list-action.is-active {
  background: var(--color-surface-selected);
  border-color: var(--color-primary);
  color: var(--color-fg);
}

.list-action.is-retry { color: var(--color-danger); border-style: dashed; }

.list-action-enter { font-size: var(--sp-t-micro); color: var(--color-primary); }
</style>
