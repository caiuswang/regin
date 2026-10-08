<script setup>
// The palette's row body when it is offering sessions rather than search hits
// — the ⌘J roster, and the rows a `#<session-id>` address resolved to.
// It replaces `SearchResultList` and nothing else: the focus trap and the key
// handling are the palette's, unchanged. Each row is the same `PaletteRow`
// search renders, with the 1–9 hotkey in front of it.
//
// Selection is reverse-video rather than a ring, because a row here is two
// lines tall and a 1.5px outline around that reads as a box, not as a cursor.
// tmux' choose-tree is the model: moving the selection previews and never
// commits.
import { computed } from 'vue'
import SearchPaletteRow from './SearchPaletteRow.vue'
import {
  GROUP_LABEL, groupOf, hotkeyFor, paletteRowOf, rosterMeta,
} from '../../utils/sessionSwitcher.js'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  attachedId: { type: String, default: null },
  clock: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: null },
  // What an empty list MEANS depends on why it is empty: the roster having
  // nothing to offer and an address resolving to nothing are different
  // answers, and one message cannot be both.
  emptyText: { type: String, default: 'No sessions yet. Start one and it appears here.' },
  emptyTitle: { type: String, default: '' },
  loadingText: { type: String, default: 'reading the roster…' },
  // The 1–9 chord is disarmed wherever the query can contain digits, and a
  // chip for a key that does nothing is worse than no chip.
  showHotkeys: { type: Boolean, default: true },
  // The preview has walked away from the cursor row: it keeps its place but
  // drops the reverse video, whose light text would sit on no fill.
  walked: { type: Boolean, default: false },
})
defineEmits(['select', 'attach'])

const groupCounts = computed(() => {
  const out = {}
  for (const row of props.rows) {
    const g = groupOf(row)
    out[g] = (out[g] || 0) + 1
  }
  return out
})

const decorated = computed(() => {
  let previous = null
  return props.rows.map((row, index) => {
    const group = groupOf(row)
    const first = group !== previous
    previous = group
    return {
      row,
      id: row.trace_id,
      first,
      group,
      groupLabel: GROUP_LABEL[group],
      groupCount: groupCounts.value[group],
      hotkey: props.showHotkeys ? (hotkeyFor(index) || '·') : '',
      palette: { ...paletteRowOf(row), here: row.trace_id === props.attachedId },
      meta: rosterMeta(row, props.clock),
    }
  })
})
</script>

<template>
  <div class="search-results switcher-list content-scroll">
    <p v-if="error" class="switcher-empty">{{ error }}</p>
    <p v-else-if="loading && !decorated.length" class="switcher-empty">
      {{ loadingText }}
    </p>
    <div v-else-if="!decorated.length && emptyTitle" class="switcher-empty is-card">
      <p class="switcher-empty-title">{{ emptyTitle }}</p>
      <p class="switcher-empty-body">{{ emptyText }}</p>
    </div>
    <p v-else-if="!decorated.length" class="switcher-empty">{{ emptyText }}</p>

    <template v-for="item in decorated" :key="item.id">
      <header v-if="item.first" class="group-head" :data-group="item.group">
        <span class="group-dot" aria-hidden="true" />
        <span class="group-label">{{ item.groupLabel }}</span>
        <span class="group-count">{{ item.groupCount }}</span>
        <span class="group-rule" />
      </header>

      <div
        :id="`search-row-${item.id}`"
        role="option"
        :aria-selected="item.id === activeId"
        class="switcher-row cursor-pointer hover:bg-surface-2"
        :class="{ 'is-active': item.id === activeId && !walked, 'is-walked': item.id === activeId && walked }"
        :data-testid="'switcher-row'"
        :data-trace-id="item.id"
        :data-group="item.group"
        @click="$emit('select', item.row)"
        @dblclick="$emit('attach', item.row)"
      >
        <span class="switcher-hotkey">{{ item.hotkey }}</span>
        <SearchPaletteRow :row="item.palette">
          <template #meta>
            <span class="switcher-meta">{{ item.meta }}</span>
          </template>
        </SearchPaletteRow>
      </div>
    </template>
  </div>
</template>

<style scoped>
.switcher-list {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0.5rem 0.625rem;
  border-right: 1px solid var(--color-border);
}

.switcher-empty {
  padding: 1.25rem;
  font-size: var(--sp-t-body);
  color: var(--color-fg-subtle);
}

.switcher-empty.is-card {
  margin: 0.75rem;
  padding: 0.875rem 1rem;
  border: 1px dashed var(--color-border-strong, var(--color-border));
  border-radius: 12px;
}

.switcher-empty-title {
  margin: 0;
  font-weight: 600;
  color: var(--color-fg);
}

.switcher-empty-body {
  margin: 0.375rem 0 0;
  font-size: var(--sp-t-meta);
  line-height: 1.6;
}

.group-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 0.625rem 0.375rem;
}

.group-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-fg-faint);
}

.group-label {
  font-size: var(--sp-t-micro);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.085em;
  color: var(--color-fg-muted);
}

.group-count {
  font-size: var(--sp-t-micro);
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

.group-rule { flex: 1; height: 1px; background: var(--color-border); }

.switcher-row {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.4375rem 0.625rem;
  border-radius: 8px;
  cursor: pointer;
}

/* Reverse video, not a ring. The extra ancestor is deliberate: it must
   outrank the `hover:bg-surface-2` utility the row also carries, which is
   otherwise the same specificity. */
.switcher-list .switcher-row.is-active { background: var(--color-fg); }

.switcher-list .switcher-row.is-walked {
  outline: 1.5px dashed var(--color-border-strong);
  outline-offset: -1.5px;
}

.switcher-hotkey {
  flex-shrink: 0;
  width: 12px;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-micro);
  line-height: 1.38rem;
  text-align: right;
  color: var(--color-fg-subtle);
  font-variant-numeric: tabular-nums;
}

.switcher-meta {
  margin-left: auto;
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

/* On the inverted row every accent is mixed from the row's OWN text colour,
   never from literal white: under dark the row is light and a white chip would
   vanish into it. */
.is-active .switcher-hotkey,
.is-active :deep(.pr-title),
.is-active :deep(.pr-ref),
.is-active :deep(.pr-status) { color: var(--color-surface); }

.is-active :deep(.pr-line.is-sub),
.is-active :deep(.pr-aside),
.is-active .switcher-meta {
  color: color-mix(in srgb, var(--color-surface) 66%, var(--color-fg));
}

.is-active :deep(.pr-chip) {
  background: transparent;
  border-color: color-mix(in srgb, var(--color-surface) 40%, transparent);
  color: var(--color-surface);
}
</style>
