<script setup>
// The palette's preview body when the Sessions facet has no query.
//
// One view, not two. The spec's earlier revisions offered a raw/terminal
// toggle beside the prose; it is gone on purpose — the raw span log lives in
// the trace, which is one ↵ away, and a preview that can show two things is a
// preview you have to configure before you can read it.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { categoryTint } from '../../utils/searchDocs.js'
import {
  elapsedKey, elapsedLabel, rowName, stateMeta, tokensLabel,
} from '../../utils/sessionSwitcher.js'

const props = defineProps({
  row: { type: Object, default: null },
  clock: { type: Object, default: null },
})
defineEmits(['attach'])

const state = computed(() => (props.row ? stateMeta(props.row) : null))

const meta = computed(() => {
  const row = props.row
  if (!row) return []
  return [
    { k: 'turn', v: `#${row.turn ?? 0}` },
    { k: elapsedKey(row), v: elapsedLabel(row, props.clock) },
    { k: 'spans', v: row.span_count == null ? '—' : String(row.span_count) },
    { k: 'context', v: row.context_pct == null ? '—' : `${Math.round(row.context_pct)}%` },
  ]
})

// A prose span is rendered as speech — a role label and its text. Everything
// else (a tool call, a rule check) collapses to one muted line: it is context
// for the prose around it, not the thing you came to read.
const PROSE_KINDS = { prompt: 'You', assistant: 'Assistant', thinking: 'Thinking' }

const chat = computed(() => (props.row?.tail || []).map((t, i) => ({
  key: `${t.ts}-${i}`,
  prose: !!PROSE_KINDS[t.kind],
  label: PROSE_KINDS[t.kind] || t.name.replace(/^tool\./, ''),
  tint: categoryTint(t.kind),
  italic: t.kind === 'thinking',
  ts: (t.ts || '').slice(11, 19),
  text: t.text,
  pending: t.pending,
})))
</script>

<template>
  <div class="search-preview switcher-preview">
    <template v-if="row && state">
      <div class="switcher-head">
        <div class="switcher-state-row">
          <span
            class="switcher-chip"
            :style="{ background: state.soft, color: state.strong }"
          >{{ state.label }}</span>
          <span class="switcher-tokens">{{ tokensLabel(row) }}</span>
        </div>
        <h2 class="switcher-title">{{ rowName(row) }}</h2>
        <p class="switcher-crumb">{{ row.repo || row.cwd || 'no repo' }}</p>
        <dl class="switcher-meta-grid">
          <div v-for="m in meta" :key="m.k">
            <dt>{{ m.k }}</dt>
            <dd>{{ m.v }}</dd>
          </div>
        </dl>
      </div>

      <!-- The facet doubles as an inbox, so a session that stopped on a
           question leads with the question rather than with its status. -->
      <div v-if="row.needs" class="switcher-needs">
        <p class="switcher-needs-label">Waiting on you</p>
        <p class="switcher-needs-body">{{ row.needs }}</p>
      </div>

      <div class="switcher-chat content-scroll">
        <p v-if="!chat.length" class="switcher-quiet">No spans yet.</p>
        <div
          v-for="line in chat"
          :key="line.key"
          class="switcher-turn"
          :class="{ 'is-prose': line.prose }"
        >
          <span class="switcher-turn-label" :style="{ color: line.tint }">
            {{ line.label }}
          </span>
          <span class="switcher-turn-text" :class="{ 'is-italic': line.italic }">
            {{ line.text }}<span v-if="line.pending" class="switcher-cursor">▍</span>
          </span>
          <span class="switcher-turn-ts">{{ line.ts }}</span>
        </div>
      </div>

      <div class="switcher-foot">
        <Button
          variant="primary"
          size="sm"
          data-testid="switcher-attach"
          @click="$emit('attach', row)"
        >
          Attach<kbd>↵</kbd>
        </Button>
      </div>
    </template>

    <div v-else class="switcher-explainer">
      <h2>Session switcher</h2>
      <p>
        Every session regin has traced recently, with what it is doing right
        now. Moving the selection previews it; <strong>↵</strong> attaches.
      </p>
    </div>
  </div>
</template>

<style scoped>
.switcher-preview {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-background);
}

.switcher-head {
  flex-shrink: 0;
  padding: 1rem 1.375rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.switcher-title {
  font-size: 1rem;
  font-weight: 700;
  color: var(--color-fg);
  line-height: 1.35;
  margin: 0.5625rem 0 0;
  overflow-wrap: anywhere;
}

.switcher-crumb {
  font-size: 0.71875rem;
  color: var(--color-fg-muted);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  margin: 0.3125rem 0 0;
  overflow-wrap: anywhere;
}

.switcher-foot {
  flex-shrink: 0;
  padding: 0.75rem 1.375rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

.switcher-foot kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.6875rem;
  opacity: 0.7;
}

.switcher-explainer { padding: 2rem 1.5rem; }

.switcher-explainer h2 {
  font-size: 0.875rem;
  font-weight: 700;
  color: var(--color-fg);
}

.switcher-explainer p {
  margin-top: 0.5rem;
  font-size: 0.8125rem;
  line-height: 1.6;
  color: var(--color-fg-muted);
}

.switcher-state-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
}

.switcher-chip {
  font-size: 0.65625rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 2px 7px;
  border-radius: 6px;
}

.switcher-tokens {
  margin-left: auto;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.6875rem;
  color: var(--color-fg-faint);
}

.switcher-meta-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1.25rem;
  margin-top: 0.625rem;
}

.switcher-meta-grid dt {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-fg-faint);
}

.switcher-meta-grid dd {
  margin: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.75rem;
  color: var(--color-fg);
  font-variant-numeric: tabular-nums;
}

.switcher-needs {
  flex-shrink: 0;
  margin: 0 1.25rem 0.625rem;
  padding: 0.5rem 0.75rem;
  border-radius: 9px;
  border: 1px solid var(--color-warning);
  background: var(--color-warning-soft);
}

.switcher-needs-label {
  font-size: 0.625rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-warning-strong);
}

.switcher-needs-body {
  margin-top: 2px;
  font-size: 0.78125rem;
  color: var(--color-fg);
}

.switcher-chat {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.875rem 1.25rem 1.125rem;
  display: flex;
  flex-direction: column;
  gap: 0.4375rem;
}

.switcher-quiet { font-size: 0.78125rem; color: var(--color-fg-subtle); }

.switcher-turn {
  display: grid;
  grid-template-columns: 66px minmax(0, 1fr) auto;
  gap: 0.5rem;
  align-items: baseline;
  font-size: 0.71875rem;
}

.switcher-turn-label {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  font-weight: 700;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher-turn-text {
  min-width: 0;
  color: var(--color-fg-subtle);
  overflow-wrap: anywhere;
}

.switcher-turn.is-prose .switcher-turn-text {
  color: var(--color-fg);
  font-size: 0.78125rem;
}

.switcher-turn-text.is-italic { font-style: italic; color: var(--color-fg-subtle); }

.switcher-turn-ts {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

.switcher-cursor {
  margin-left: 2px;
  color: var(--color-success);
  animation: switcher-blink 1.1s steps(2, start) infinite;
}

@keyframes switcher-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  .switcher-cursor { animation: none; }
}
</style>
