<script setup>
// Everything a Pipeline list shows when it has no rows to show — and it
// always says which kind of nothing it is (I5): nothing exists, nothing
// matches, or this page is past the end. Three causes, three remedies; one
// sentence for all of them sends a reader to change a filter that is working.
import { computed } from 'vue'
import Button from './ui/Button.vue'

const props = defineProps({
  //: 'loading' | 'error' | 'empty' | 'no-match' | 'past-end' | 'rows'
  state: { type: String, required: true },
  noun: { type: String, required: true },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 0 },
  dirtyCount: { type: Number, default: 0 },
  error: { type: String, default: '' },
  // The `empty` sentence, when the tab has something better to say than the
  // default — a queue that is clear is not a ledger that is empty.
  emptyText: { type: String, default: '' },
})
const emit = defineEmits(['retry', 'clear', 'first-page'])

const plural = computed(() => `${props.noun}s`)
const filters = computed(() => (props.dirtyCount === 1 ? 'this filter'
  : props.dirtyCount > 1 ? `these ${props.dirtyCount} filters` : 'this view'))
</script>

<template>
  <div v-if="state !== 'rows'" class="ls" :data-state="state" data-testid="list-status">
    <div v-if="state === 'loading'" class="ls-skeleton" aria-busy="true" aria-label="Loading">
      <div v-for="n in 3" :key="n" class="ls-skeleton-row" />
    </div>

    <div v-else-if="state === 'error'" class="ls-box ls-error" role="alert">
      <p>{{ error || `The ${plural} could not be read.` }}</p>
      <Button variant="secondary" size="sm" data-testid="list-status-retry" @click="emit('retry')">
        Retry
      </Button>
    </div>

    <div v-else-if="state === 'empty'" class="ls-box">
      <p>{{ emptyText || `No ${plural} yet.` }}</p>
      <slot name="create" />
    </div>

    <div v-else-if="state === 'no-match'" class="ls-box">
      <p>No {{ plural }} match {{ filters }}.</p>
      <Button
        v-if="dirtyCount"
        variant="secondary"
        size="sm"
        data-testid="list-status-clear"
        @click="emit('clear')"
      >Clear</Button>
    </div>

    <div v-else-if="state === 'past-end'" class="ls-box">
      <p>Page {{ page + 1 }} is past the end of {{ total }}.</p>
      <Button variant="secondary" size="sm" data-testid="list-status-first" @click="emit('first-page')">
        First page
      </Button>
    </div>
  </div>
</template>

<style scoped>
.ls-box {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: 0.5rem 0.75rem;
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
    padding: 1rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    text-align: center;
}
.ls-error { border-style: solid; border-color: var(--color-danger, #b91c1c); color: var(--color-danger, #b91c1c); }
.ls-skeleton { display: flex; flex-direction: column; gap: 0.5rem; }
.ls-skeleton-row {
    height: 2.25rem;
    border-radius: var(--radius-md, 0.375rem);
    background: var(--color-surface-2);
    animation: ls-pulse 1.4s ease-in-out infinite;
}
@keyframes ls-pulse { 50% { opacity: 0.5; } }
@media (prefers-reduced-motion: reduce) {
    .ls-skeleton-row { animation: none; }
}
</style>
