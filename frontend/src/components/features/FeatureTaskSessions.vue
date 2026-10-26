<script setup>
// A task row's sessions: a count that is always visible, and the rows behind
// it on demand. Two roots on purpose — the toggle is the task row's last grid
// cell, and the list spans the whole row under it (`grid-column: 1 / -1`), so
// opening it moves no other row's columns.
//
// The title shown is the session's own unless it is only a skill line; the
// task the row sits under already says what the session built.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { costSum, money, plural } from '../../utils/ledgerFormat'

const props = defineProps({
  taskId: { type: Number, required: true },
  rows: { type: Array, default: () => [] },
  canOpenTrace: { type: Boolean, default: true },
})

const open = ref(false)
const summary = computed(() =>
  `${plural(props.rows.length, 'session')} · ${costSum(props.rows)}`)
const listId = computed(() => `ft-sessions-${props.taskId}`)

function cost(r) {
  if (r.cost_usd === null || r.cost_usd === undefined) return 'no usage'
  if (r.cost_usd === 0 && !r.is_primary) return 'charged elsewhere'
  return money(r.cost_usd)
}
</script>

<template>
  <span class="ledger-cell" data-cell="sessions">
    <Button
      v-if="rows.length"
      variant="ghost" size="sm" class="fts-toggle"
      :aria-expanded="open" :aria-controls="listId"
      :aria-label="`${open ? 'Hide' : 'Show'} ${summary} of task #${taskId}`"
      :title="summary"
      :data-testid="`feature-task-sessions-${taskId}`"
      @click="open = !open"
    >
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="11" />
      <span class="ledger-ellipsis">{{ summary }}</span>
    </Button>
  </span>
  <ul
    v-if="open && rows.length" :id="listId" class="fts-list"
    :data-testid="`feature-task-sessions-list-${taskId}`"
  >
    <li v-for="r in rows" :key="r.trace_id" class="fts-row">
      <span class="fts-date">{{ r.date }}</span>
      <component
        :is="canOpenTrace ? 'router-link' : 'span'"
        :to="canOpenTrace ? `/trace/sessions/${r.trace_id}` : undefined"
        class="fts-id" :class="canOpenTrace ? 'fts-id-link' : ''"
        :title="r.trace_id"
      >{{ r.short_trace }}</component>
      <span v-if="r.command" class="fts-cmd">{{ r.command }}</span>
      <span v-else class="fts-title" :title="r.title">{{ r.title || '—' }}</span>
      <span class="fts-cost" :class="{ 'is-gap': r.cost_usd === null }">{{ cost(r) }}</span>
    </li>
  </ul>
</template>

<style scoped>
.fts-toggle {
    min-width: 0; max-width: 100%; gap: 0.2rem; height: 1.5rem; padding: 0 0.4rem;
    font-size: 11px; font-weight: 400; font-variant-numeric: tabular-nums;
}
.fts-list {
    grid-column: 1 / -1; min-width: 0; margin: 0.25rem 0 0; padding: 0 0 0 1.4rem;
    list-style: none; display: flex; flex-direction: column; gap: 0.15rem;
}
.fts-row {
    display: flex; align-items: center; gap: 0.6rem; min-width: 0; font-size: 12px;
}
.fts-date {
    width: 5.5rem; flex: none; font-family: var(--font-mono, monospace);
    font-size: 11px; color: var(--color-fg-faint); font-variant-numeric: tabular-nums;
}
.fts-id {
    width: 4.75rem; flex: none; font-family: var(--font-mono, monospace);
    font-size: 11.5px; color: var(--color-fg-subtle);
}
.fts-id-link { color: var(--color-primary); text-decoration: none; }
.fts-id-link:hover { text-decoration: underline; }
.fts-cmd {
    flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-family: var(--font-mono, monospace);
    font-size: 11px; color: var(--color-fg-subtle);
}
.fts-title {
    flex: 1 1 auto; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; color: var(--color-fg-muted);
}
.fts-cost {
    flex: none; margin-left: auto; font-family: var(--font-mono, monospace);
    font-size: 11.5px; font-variant-numeric: tabular-nums; color: var(--color-fg);
}
.fts-cost.is-gap { color: var(--color-amber-700); font-style: italic; }
@media (max-width: 640px) {
    .fts-list { padding-left: 0.5rem; }
    .fts-date { width: auto; }
}
</style>
