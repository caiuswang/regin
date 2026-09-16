<script setup>
// The palette's row body when it is offering sessions rather than search hits
// — the ⌘J roster, and the rows a `#<session-id>` address resolved to.
// It replaces `SearchResultList` and nothing else: the shell, the focus trap,
// the facet bar and the key handling are the palette's, unchanged.
//
// Selection is reverse-video rather than a ring, because a row here carries
// four lines of its own and a 1.5px outline around that reads as a box, not as
// a cursor. tmux' choose-tree is the model: moving the selection previews and
// never commits.
import { computed } from 'vue'
import {
  GROUP_LABEL, groupOf, hotkeyFor, lastLine, rowName, stateMeta,
  elapsedLabel,
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
  loadingText: { type: String, default: 'reading the roster…' },
  // The 1–9 chord is disarmed wherever the query can contain digits, and a
  // chip for a key that does nothing is worse than no chip.
  showHotkeys: { type: Boolean, default: true },
})
defineEmits(['select', 'attach'])

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
      groupLabel: GROUP_LABEL[group],
      groupCount: props.rows.filter(r => groupOf(r) === group).length,
      hotkey: props.showHotkeys ? hotkeyFor(index) : '',
      state: stateMeta(row),
      name: rowName(row),
      meta: `${elapsedLabel(row, props.clock)} · turn ${row.turn ?? 0}`,
      lastLine: lastLine(row),
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
    <p v-else-if="!decorated.length" class="switcher-empty">{{ emptyText }}</p>

    <template v-for="item in decorated" :key="item.id">
      <header v-if="item.first" class="group-head">
        <span class="group-label">{{ item.groupLabel }}</span>
        <span class="group-count">{{ item.groupCount }}</span>
        <span class="group-rule" />
      </header>

      <div
        :id="`search-row-${item.id}`"
        role="option"
        :aria-selected="item.id === activeId"
        class="switcher-row cursor-pointer hover:bg-surface-2"
        :class="{ 'is-active': item.id === activeId }"
        :data-testid="'switcher-row'"
        :data-trace-id="item.id"
        @click="$emit('select', item.row)"
        @dblclick="$emit('attach', item.row)"
      >
        <span class="switcher-hotkey">{{ item.hotkey }}</span>
        <span
          class="switcher-dot"
          :class="{ 'is-pulsing': item.state.pulse }"
          :style="{ '--dot': item.state.dot }"
          :aria-hidden="true"
        >{{ item.state.glyph }}</span>

        <div class="switcher-body">
          <div class="switcher-title-row">
            <span class="switcher-name">{{ item.name }}</span>
            <span v-if="item.id === attachedId" class="switcher-here">here</span>
            <span class="switcher-meta">{{ item.meta }}</span>
          </div>
          <div class="switcher-sub">
            <span class="switcher-repo">{{ item.row.repo || item.row.cwd || '—' }}</span>
            <span class="switcher-state" :style="{ color: item.state.strong }">
              {{ item.state.label }}
            </span>
          </div>
          <p v-if="item.lastLine" class="switcher-line">{{ item.lastLine }}</p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.switcher-list {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0.5rem 0;
  border-right: 1px solid var(--color-border);
}

.switcher-empty {
  padding: 1.25rem;
  font-size: 0.8125rem;
  color: var(--color-fg-subtle);
}

.group-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1.25rem 0.375rem;
}

.group-label {
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-fg-subtle);
}

.group-count {
  font-size: 0.65625rem;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

.group-rule { flex: 1; height: 1px; background: var(--color-border); }

.switcher-row {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.5rem 1.25rem;
  cursor: pointer;
}

/* Reverse video, not a ring — the row is four lines tall and an outline
   around it reads as a container rather than as the cursor. The extra
   ancestor is deliberate: it must outrank the `hover:bg-surface-2` utility
   the row also carries, which is otherwise the same specificity. */
.switcher-list .switcher-row.is-active { background: var(--color-fg); }

.switcher-hotkey {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 5px;
  background: var(--color-surface-2);
  color: var(--color-fg-subtle);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  font-variant-numeric: tabular-nums;
}

/* On the inverted row every accent is mixed from the row's OWN text colour,
   never from literal white: under dark the row is light and a white chip would
   vanish into it. */
.is-active .switcher-hotkey {
  background: color-mix(in srgb, var(--color-surface) 16%, transparent);
  color: var(--color-surface);
}

.switcher-dot {
  flex-shrink: 0;
  margin-top: 1px;
  font-size: 0.5625rem;
  line-height: 18px;
  color: var(--dot);
}

.switcher-dot.is-pulsing { animation: switcher-pulse 2s ease-in-out infinite; }

@keyframes switcher-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

@media (prefers-reduced-motion: reduce) {
  .switcher-dot.is-pulsing { animation: none; }
}

.switcher-body { min-width: 0; flex: 1; }

.switcher-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.4375rem;
}

.switcher-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-fg);
}

.is-active .switcher-name { color: var(--color-surface); }

.switcher-here {
  flex-shrink: 0;
  font-size: 0.625rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--color-info-soft);
  color: var(--color-info-strong);
}

.is-active .switcher-here {
  background: color-mix(in srgb, var(--color-surface) 18%, transparent);
  color: var(--color-surface);
}

.switcher-meta {
  margin-left: auto;
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.65625rem;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

.switcher-sub {
  display: flex;
  align-items: baseline;
  gap: 0.4375rem;
  margin-top: 1px;
  font-size: 0.6875rem;
}

.switcher-repo {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  color: var(--color-fg-muted);
}

.switcher-state { font-weight: 600; }

.switcher-line {
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.6875rem;
  color: var(--color-fg-subtle);
}

.is-active .switcher-meta,
.is-active .switcher-repo,
.is-active .switcher-line {
  color: color-mix(in srgb, var(--color-surface) 66%, var(--color-fg));
}

.is-active .switcher-state { color: var(--color-surface) !important; }
</style>
