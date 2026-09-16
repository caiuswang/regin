<script setup>
// The ambient half of the switcher.
//
// A palette is modal by nature: you have to ask it a question. The rail asks
// nothing — it sits under every trace view, one cell per recent session, so a
// session that starts waiting on you is visible without opening anything.
// Click a cell or press its ⌃number; the palette is not involved.
//
// It is a flex sibling of `.content-scroll` inside `main.content`, not a fixed
// overlay, so it takes its band out of the layout instead of covering the last
// rows of whatever you were reading.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Button from './ui/Button.vue'
import { useSessionSwitcher } from '../composables/useSessionSwitcher.js'
import {
  elapsedLabel, hotkeyFor, rowName, stateMeta, tokensLabel,
} from '../utils/sessionSwitcher.js'

const route = useRoute()
const switcher = useSessionSwitcher()

// The rail belongs to the reader, not to the app: it appears once a session
// trace is open and nowhere else.
const traceId = computed(() => {
  const m = /^\/trace\/sessions\/([^/]+)/.exec(route.path)
  return m ? decodeURIComponent(m[1]) : null
})

const visible = computed(() => !!traceId.value && switcher.rows.value.length > 0)

const attached = computed(() =>
  switcher.rows.value.find(r => r.trace_id === traceId.value) || null)

const cells = computed(() => switcher.rows.value.map((row, i) => ({
  row,
  id: row.trace_id,
  hotkey: hotkeyFor(i),
  state: stateMeta(row),
  name: rowName(row),
  // The TITLE, not the repo: in the common case every open session is in the
  // same checkout, and a rail of twelve cells all reading "regin" names
  // nothing. The repo rides the tooltip with the state.
  short: rowName(row),
  here: row.trace_id === traceId.value,
  // Context fill is the only honest "how far along" regin holds — a session
  // has no task list to measure against, and an ETA would be invented.
  pct: row.context_pct == null ? null : Math.min(100, Math.round(row.context_pct)),
})))

function hold() {
  if (traceId.value) switcher.hold()
  else switcher.release()
}

// The trace view floats two controls against the VIEWPORT bottom (back-to-top,
// follow-latest), which the rail would otherwise sit under. Publishing its
// height as a custom property is what lets those offset by exactly the band
// the rail takes, with no component needing to know the rail exists.
const RAIL_H = '--trace-rail-h'
const el = ref(null)

async function publishHeight() {
  await nextTick()
  const px = visible.value && el.value ? el.value.offsetHeight : 0
  document.documentElement.style.setProperty(RAIL_H, `${px}px`)
}

onMounted(() => { hold(); publishHeight() })
watch(traceId, hold)
watch([visible, cells], publishHeight)
onBeforeUnmount(() => document.documentElement.style.removeProperty(RAIL_H))
</script>

<template>
  <div v-if="visible" ref="el" class="trace-rail" data-testid="trace-rail">
    <div v-if="attached" class="rail-attached">
      <span
        class="rail-dot"
        :class="{ 'is-pulsing': stateMeta(attached).pulse }"
        :style="{ '--dot': stateMeta(attached).dot }"
        aria-hidden="true"
      />
      <span class="rail-attached-name">{{ rowName(attached) }}</span>
      <span class="rail-attached-meta">
        {{ attached.repo || '—' }} · turn #{{ attached.turn ?? 0 }} ·
        {{ elapsedLabel(attached, switcher.clock.value) }} · {{ tokensLabel(attached) }}
      </span>
    </div>

    <div class="rail-cells" role="group" aria-label="Sessions">
      <Button
        v-for="cell in cells"
        :key="cell.id"
        variant="ghost"
        size="sm"
        class="rail-cell"
        :class="{ 'is-here': cell.here }"
        :style="{ '--dot': cell.state.dot }"
        :data-testid="'rail-cell'"
        :data-trace-id="cell.id"
        :aria-current="cell.here ? 'page' : undefined"
        :title="`${cell.name} — ${cell.state.label}${cell.row.repo ? ' · ' + cell.row.repo : ''}`"
        @click="switcher.attach(cell.row)"
      >
        <span class="rail-num">{{ cell.hotkey }}</span>
        <span
          class="rail-dot"
          :class="{ 'is-pulsing': cell.state.pulse }"
          aria-hidden="true"
        />
        <span class="rail-short">{{ cell.short }}</span>
        <span v-if="cell.pct != null" class="rail-bar" aria-hidden="true">
          <span class="rail-bar-fill" :style="{ width: `${cell.pct}%` }" />
        </span>
      </Button>
    </div>

    <span class="rail-hint"><kbd>⌘J</kbd> switch</span>
  </div>
</template>

<style scoped>
.trace-rail {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.375rem 0.75rem 0.125rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-background);
  font-size: 0.6875rem;
  overflow-x: auto;
}

.rail-attached {
  display: flex;
  align-items: center;
  gap: 0.4375rem;
  flex-shrink: 0;
  max-width: 34%;
}

.rail-attached-name {
  font-weight: 600;
  color: var(--color-fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-attached-meta {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  color: var(--color-fg-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rail-cells {
  display: flex;
  align-items: stretch;
  gap: 0.25rem;
  min-width: 0;
  overflow-x: auto;
  /* A 28px strip of pills in a 41px band has nowhere for a scrollbar to go:
     drawn over the content it hides the very names it is scrolling. Give it
     its own lane, taken back out of the rail's bottom padding so the band
     keeps its height. Styling the webkit bar also opts out of macOS overlay
     scrollbars, which ignore the reserved space. */
  padding-bottom: 5px;
  scrollbar-width: thin;
  scrollbar-color: var(--color-slate-300) transparent;
}

.rail-cells::-webkit-scrollbar { height: 5px; }
.rail-cells::-webkit-scrollbar-track { background: transparent; }

.rail-cells::-webkit-scrollbar-thumb {
  background: var(--color-slate-300);
  border-radius: 3px;
}

.rail-cells::-webkit-scrollbar-thumb:hover { background: var(--color-slate-400); }

.rail-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  flex-shrink: 0;
  max-width: 13rem;
  padding: 0.25rem 0.5rem;
  border: 0;
  border-top: 2px solid transparent;
  border-radius: 0 0 6px 6px;
  background: transparent;
  color: var(--color-fg-muted);
  cursor: pointer;
  font-size: 0.6875rem;
}

.rail-cell:hover { background: var(--color-surface-2); }

.rail-cell:focus-visible {
  outline: 2px solid var(--color-ring, var(--color-primary));
  outline-offset: -2px;
}

/* The attached cell is LIFTED — a top rule in its own state colour and a
   raised surface — never a different hue. Recolouring it would make "which
   session am I in" and "what state is it in" fight over the same channel. */
.rail-cell.is-here {
  background: var(--color-surface);
  border-top-color: var(--dot);
  color: var(--color-fg);
  font-weight: 600;
}

.rail-num {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
  min-width: 0.5rem;
}

.rail-cell.is-here .rail-num { color: var(--dot); }

.rail-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dot);
}

.rail-dot.is-pulsing { animation: rail-pulse 2s ease-in-out infinite; }

@keyframes rail-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

@media (prefers-reduced-motion: reduce) {
  .rail-dot.is-pulsing { animation: none; }
}

.rail-short {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail-bar {
  flex-shrink: 0;
  width: 26px;
  height: 3px;
  border-radius: 2px;
  background: var(--color-surface-3, var(--color-surface-2));
  overflow: hidden;
}

.rail-bar-fill { display: block; height: 100%; background: var(--dot); }

.rail-hint {
  margin-left: auto;
  flex-shrink: 0;
  color: var(--color-fg-faint);
  white-space: nowrap;
}

.rail-hint kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 4px;
  border-radius: 4px;
}

/* On a phone the identity line and the keyboard advice are both noise — the
   cells are the whole point, and they need every pixel. */
@media (max-width: 767px) {
  .rail-attached-meta,
  .rail-hint { display: none; }
  .rail-attached { max-width: 40%; }
}
</style>
