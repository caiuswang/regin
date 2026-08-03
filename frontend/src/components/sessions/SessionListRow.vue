<script setup>
// Desktop session-list row. A CSS grid rather than a <tr>: the row's identity
// column stacks a title line over a metadata line, which a table cell can lay
// out but can't keep aligned with the sticky column header without a shared
// track definition — the grid template lives once in `--session-cols`
// (SessionsView) and both the header and every row read it.
import { computed } from 'vue'
import { shortTraceId } from '../../utils/traceFormatters.js'
import { fmtRelativeAge, isActiveWithClock, serverAgeMs } from '../../utils/sessionActivity.js'
import {
  activityMoreLabel, activityMoreTitle, fmtDuration, otherRepoTitle,
  primaryRepo, shortTestName, timeTitle, titlePreview, totalMs,
} from '../../utils/sessionRowFormat.js'
import { useCopy } from '../../composables/useCopy.js'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import SessionTags from '../SessionTags.vue'
import SessionAgentIcon from './SessionAgentIcon.vue'
import SessionContextMeter from './SessionContextMeter.vue'
import { useNotificationCenter } from '../../composables/useNotificationCenter.js'

const props = defineProps({
  s: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  isDeleting: { type: Boolean, default: false },
  isClosing: { type: Boolean, default: false },
  // { local, utc, atMs } server-clock anchor from the list envelope
  // (useServerClock) — keeps "ago" / active ages timezone-safe.
  clock: { type: Object, default: null },
})

const emit = defineEmits(['toggle', 'delete', 'close', 'add-tag', 'remove-tag'])

const { copyText } = useCopy()
// The list is where you go looking for "which one needs me". A blocker that
// only lives in a banner you dismissed leaves that question unanswered here.
const { awaitingTraceId, resumedTraceId, blockerWaitedFor } = useNotificationCenter()

const awaiting = computed(() => awaitingTraceId.value === props.s.trace_id)
const resumed = computed(() => resumedTraceId.value === props.s.trace_id)

const active = computed(() => isActiveWithClock(props.s, props.clock))
const repo = computed(() => primaryRepo(props.s))
const idLabel = computed(() => `${shortTraceId(props.s.trace_id, 12)}…`)

const statusLabel = computed(() => {
  if (active.value) return 'active'
  if (props.s.status === 'ended') return props.s.ended_reason === 'manual' ? 'closed' : 'ended'
  return 'idle'
})

const statusTitle = computed(() => {
  if (active.value) {
    return props.s.status === 'active'
      ? 'SessionStart fired without a matching SessionEnd'
      : 'No explicit lifecycle — last span within 10 minutes'
  }
  if (props.s.status === 'ended') {
    if (props.s.ended_reason === 'manual') return 'Manually closed'
    return props.s.ended_reason ? `reason: ${props.s.ended_reason}` : 'SessionEnd fired'
  }
  return 'No lifecycle marker and no recent spans'
})
</script>

<template>
  <div
    class="srow"
    :class="{ 'srow--selected': selected, 'srow--active': active, 'srow--awaiting': awaiting }"
  >
    <div class="srow__cell srow__check">
      <Checkbox
        :model-value="selected"
        @update:model-value="(v) => emit('toggle', v)"
        :aria-label="`Select session ${shortTraceId(s.trace_id)}`"
      />
    </div>

    <div class="srow__cell srow__icon">
      <SessionAgentIcon :s="s" />
    </div>

    <div class="srow__cell srow__ident">
      <router-link :to="`/trace/sessions/${s.trace_id}`" class="srow__title" :title="s.title || undefined">
        <span v-if="s.title">{{ titlePreview(s.title) }}</span>
        <span v-else class="srow__title-empty">no prompt</span>
      </router-link>
      <div class="srow__meta">
        <span
          v-if="awaiting"
          class="srow__awaiting"
          :title="`The agent is paused, waiting ${blockerWaitedFor} for your answer`"
        >
          <span class="srow__awaiting-dot" aria-hidden="true"></span>
          awaiting decision · {{ blockerWaitedFor }}
        </span>
        <span v-else-if="resumed" class="srow__resumed">resumed</span>
        <span class="srow__status" :class="`srow__status--${statusLabel}`" :title="statusTitle">
          <span class="srow__status-dot" aria-hidden="true"></span>{{ statusLabel }}
        </span>
        <code class="srow__id" :title="s.trace_id">{{ idLabel }}</code>
        <Button
          variant="ghost"
          size="sm"
          class="srow__copy focus-visible:outline-2 focus-visible:outline-blue-500"
          title="Copy session id"
          :aria-label="`Copy session id ${s.trace_id}`"
          @click.stop="copyText(s.trace_id)"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5.5" y="5.5" width="8" height="8" rx="1.2" />
            <path d="M3.5 10.5h-1a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v1" />
          </svg>
        </Button>
        <span v-if="s.is_test" class="srow__test" title="Span attributes carry is_test=true">test</span>
        <span v-if="s.is_test && s.test_name" class="srow__test-name" :title="s.test_name">{{ shortTestName(s.test_name) }}</span>
        <SessionTags
          :tags="s.tags || []"
          :trace-id="s.trace_id"
          @add="(slug) => emit('add-tag', slug)"
          @remove="(slug) => emit('remove-tag', slug)"
        />
      </div>
    </div>

    <div class="srow__cell srow__repo">
      <template v-if="repo">
        <span class="srow__repo-chip" :title="s.cwd || repo">{{ repo }}</span>
        <span v-if="s.is_multi_repo" class="srow__repo-more" :title="otherRepoTitle(s)">+{{ s.repos.length - 1 }}</span>
      </template>
      <span v-else class="srow__dash" title="No registered repo matched">-</span>
    </div>

    <div class="srow__cell srow__activity">
      <div class="srow__activity-main">
        <span class="srow__num">{{ s.span_count }}</span> <span class="srow__unit">spans</span>
        <span class="srow__dot">·</span>
        <span class="srow__num">{{ s.file_edits }}</span> <span class="srow__unit">edits</span>
      </div>
      <div v-if="activityMoreLabel(s)" class="srow__activity-more" :title="activityMoreTitle(s)">{{ activityMoreLabel(s) }}</div>
    </div>

    <div class="srow__cell srow__context">
      <SessionContextMeter :s="s" />
    </div>

    <div class="srow__cell srow__elapsed">
      {{ fmtDuration(totalMs(s)) }}
      <template v-if="s.active_work_ms != null">
        <span class="srow__slash">/</span>
        <span
          class="srow__work"
          :title="`agent work time${s.active_pct != null ? ` (${s.active_pct}%)` : ''}, idle ${fmtDuration(s.idle_ms)} excluded`"
        >{{ fmtDuration(s.active_work_ms) }}</span>
      </template>
    </div>

    <div class="srow__cell srow__seen" :title="timeTitle(s)">
      {{ fmtRelativeAge(serverAgeMs(s.last_seen, clock)) }}
    </div>

    <div class="srow__actions">
      <router-link
        :to="`/live/${s.trace_id}`"
        class="srow__action srow__action--live"
        :title="`Watch session ${shortTraceId(s.trace_id, 12)}… in the live view`"
      >Live</router-link>
      <Button
        v-if="s.status !== 'ended'"
        variant="link"
        size="sm"
        class="srow__action focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="isClosing"
        :title="`Mark session ${shortTraceId(s.trace_id, 12)}… as closed (keeps its trace data)`"
        @click.stop="emit('close', s)"
      >{{ isClosing ? 'Closing…' : 'Close' }}</Button>
      <Button
        variant="link"
        size="sm"
        class="srow__action srow__action--danger focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="isDeleting"
        :title="`Delete session ${shortTraceId(s.trace_id, 12)}… and all its trace data`"
        @click.stop="emit('delete', s)"
      >{{ isDeleting ? 'Deleting…' : 'Delete' }}</Button>
    </div>
  </div>
</template>

<style scoped>
.srow {
  align-items: center;
  border-radius: 0.75rem;
  column-gap: 0.75rem;
  display: grid;
  grid-template-columns: var(--session-cols);
  padding: 0.5625rem 0.875rem;
  position: relative;
  transition: background 0.12s ease;
}
.srow:hover { background: var(--color-surface-2); }
.srow--selected { background: var(--color-blue-50); }
.srow--selected:hover { background: var(--color-blue-50); }

/* A parked agent outranks selection: it is the one row you have to act on. */
.srow--awaiting,
.srow--awaiting:hover {
  background: var(--color-warning-soft);
  box-shadow: inset 3px 0 0 var(--color-danger);
}

.srow__awaiting,
.srow__resumed {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  flex-shrink: 0;
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
}

.srow__awaiting { background: var(--color-red-100); color: var(--color-red-700); }
.srow__resumed { background: var(--color-emerald-100); color: var(--color-emerald-700); }

.srow__awaiting-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-danger);
  animation: srow-blink 1.1s steps(1, end) infinite;
}

@keyframes srow-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.15; }
}

@media (prefers-reduced-motion: reduce) {
  .srow__awaiting-dot { animation: none; }
}
.srow:focus-within { background: var(--color-surface-2); }

.srow__cell { min-width: 0; }
.srow__check { display: flex; }
.srow__icon { display: flex; }

.srow__title {
  color: var(--color-fg);
  display: block;
  font-size: 0.8125rem;
  font-weight: 600;
  overflow: hidden;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.srow__title:hover { color: var(--color-blue-800); text-decoration: underline; }
.srow__title:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; border-radius: 0.25rem; }
.srow__title-empty { color: var(--color-fg-faint); font-style: italic; font-weight: 400; }

.srow__meta {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-top: 0.25rem;
  min-width: 0;
}
.srow__status {
  align-items: center;
  display: inline-flex;
  font-size: 0.59375rem;
  font-weight: 700;
  gap: 0.3125rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.srow__status-dot {
  border-radius: 9999px;
  height: 0.3125rem;
  width: 0.3125rem;
  background: currentColor;
}
.srow__status--active { color: var(--color-emerald-700); }
.srow__status--active .srow__status-dot { animation: srow-pulse 2s ease-in-out infinite; }
.srow__status--ended,
.srow__status--closed,
.srow__status--idle { color: var(--color-fg-faint); }
@keyframes srow-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
@media (prefers-reduced-motion: reduce) {
  .srow__status--active .srow__status-dot { animation: none; }
}

.srow__id {
  color: var(--color-fg-faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
}
.srow__copy {
  align-items: center;
  background: transparent;
  border: 0;
  border-radius: 0.25rem;
  color: var(--color-fg-faint);
  cursor: pointer;
  display: inline-flex;
  height: 1rem;
  justify-content: center;
  opacity: 0;
  padding: 0;
  transition: opacity 0.12s ease, color 0.12s ease;
  width: 1rem;
}
.srow:hover .srow__copy,
.srow:focus-within .srow__copy,
.srow__copy:focus-visible { opacity: 1; }
.srow__copy:hover { color: var(--color-fg-muted); }
.srow__copy:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
.srow__copy svg {
  fill: none;
  height: 0.75rem;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.4;
  width: 0.75rem;
}
@media (hover: none) {
  .srow__copy { opacity: 1; }
}

.srow__test {
  background: var(--color-amber-100);
  border-radius: 0.25rem;
  color: var(--color-amber-700);
  font-size: 0.59375rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 0.0625rem 0.3125rem;
  text-transform: uppercase;
}
.srow__test-name {
  color: var(--color-fg-subtle);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.625rem;
  max-width: 12rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.srow__repo { display: flex; align-items: center; gap: 0.25rem; }
.srow__repo-chip {
  background: var(--color-surface-2);
  border-radius: 0.375rem;
  color: var(--color-fg-muted);
  font-size: 0.6875rem;
  max-width: 100%;
  overflow: hidden;
  padding: 0.125rem 0.4375rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.srow__repo-more { color: var(--color-fg-faint); cursor: help; font-size: 0.6875rem; }
.srow__dash { color: var(--color-fg-faint); font-size: 0.75rem; }

.srow__activity-main { font-size: 0.75rem; white-space: nowrap; }
.srow__num { color: var(--color-fg); font-variant-numeric: tabular-nums; font-weight: 600; }
.srow__unit { color: var(--color-fg-faint); }
.srow__dot { color: var(--color-border-strong); padding: 0 0.125rem; }
.srow__activity-more {
  color: var(--color-fg-faint);
  cursor: help;
  font-size: 0.6875rem;
  margin-top: 0.125rem;
}

/* A months-long session can print "2d11h50m28s / 34m2s"; clip rather than let
 * it bleed into Last seen, which reads as two columns fused into one. */
.srow__elapsed {
  color: var(--color-fg-muted);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.srow__slash { color: var(--color-border-strong); }
.srow__work { color: var(--color-fg-faint); cursor: help; }
.srow__seen {
  color: var(--color-fg-faint);
  font-size: 0.75rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Actions ride above the two right-hand columns rather than claiming a track
 * of their own: at 1440px those 210px would otherwise be permanently reserved
 * for controls that are only relevant to the one row under the pointer. */
.srow__actions {
  align-items: center;
  background: linear-gradient(to right, transparent, var(--color-surface-2) 12%);
  border-radius: 0.75rem;
  bottom: 0;
  display: flex;
  gap: 0.75rem;
  justify-content: flex-end;
  opacity: 0;
  padding: 0 0.875rem 0 2.5rem;
  pointer-events: none;
  position: absolute;
  right: 0;
  top: 0;
  transition: opacity 0.12s ease;
}
.srow:hover .srow__actions,
.srow:focus-within .srow__actions { opacity: 1; pointer-events: auto; }
.srow--selected .srow__actions { background: linear-gradient(to right, transparent, var(--color-blue-50) 12%); }
@media (hover: none) {
  /* Touch pointers can't reveal on hover — keep the actions permanently on. */
  .srow__actions { opacity: 1; pointer-events: auto; }
}

.srow__action {
  background: transparent;
  border: 0;
  color: var(--color-fg-muted);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  padding: 0;
  text-decoration: none;
  white-space: nowrap;
}
.srow__action:hover:not(:disabled) { color: var(--color-fg); text-decoration: underline; }
.srow__action:disabled { cursor: wait; opacity: 0.5; }
.srow__action:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; border-radius: 0.25rem; }
.srow__action--live { color: var(--color-emerald-700); }
.srow__action--live:hover { color: var(--color-emerald-600); }
.srow__action--danger { color: var(--color-red-600); }
.srow__action--danger:hover:not(:disabled) { color: var(--color-red-700); }
</style>
