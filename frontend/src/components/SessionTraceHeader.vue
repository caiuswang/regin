<script setup>
// Session metadata header for the trace view: eyebrow, title (+ source badge,
// expand, live/ended status), the identity + pivot chip row (trace id, model,
// start clock, plans, workflow runs, tasks), the expandable plan/workflow/task
// lists, the segmented view-mode switcher, and the reload control.
//
// Collapsed state (`collapsed` prop): the whole detail surface (eyebrow,
// title row, meta/chips, expanded lists) swaps for a single compact row —
// status dot, title, the parent-computed mono `digest`, switcher, Reload —
// while the parent folds the vitals/overview/spend strips away. The Details
// toggle (also bound to the H key in the parent) lives on the right in both
// states.
//
// The volume stats — spans, duration, active%, context%, cache, total tokens —
// deliberately do NOT live here any more: they are cells in TraceVitalsStrip,
// which gives each a stable slot instead of letting them reflow this paragraph.
// Cache read/write survives in the Overview panel's session bill, where it also
// carries a dollar figure.
//
// Self-contained by design: it takes the raw session + collections + the few
// spans-derived facts it can't compute alone (snapshotStaleAt,
// workflowParentTo), and derives everything else (title, task summary)
// internally. It owns only presentational toggle state and emits intent
// (`reload`, `jump-to-task`, `update:viewMode`) back to the parent, which
// still owns the data model.
import { ref, computed } from 'vue'
import SessionAttribution from './trace/SessionAttribution.vue'
import RefBacklinks from './refs/RefBacklinks.vue'
import SessionHeaderFacts from './trace/SessionHeaderFacts.vue'
import SessionHeaderPivots from './trace/SessionHeaderPivots.vue'
import { sessionLivenessWithClock } from '../utils/sessionActivity.js'
import { statusOfRow, statusLabel as labelOf } from '../utils/sessionStatus.js'
import { MODE_OPTIONS } from '../composables/useViewMode.js'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import TraceProjectChip from './TraceProjectChip.vue'
import TraceViewModeMenu from './TraceViewModeMenu.vue'
import TraceDetailControl from './trace/TraceDetailControl.vue'

// Date formatters kept local (exact copies of SessionTraceView's) rather than
// imported: traceFormatters exposes a differently-behaved `fmtTime` (HH:MM), so
// importing it would silently change how the snapshot time, plan dates, and the
// "updated" clock render here. Unifying the siblings is a separate follow-up.
function fmtLocalClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}
const props = defineProps({
  session: { type: Object, required: true },
  plans: { type: Array, default: () => [] },
  workflowRuns: { type: Array, default: () => [] },
  viewMode: { type: String, default: 'conversation' },
  // Per-mode row counts for the switcher (e.g. { terminal: 38, messages: 3 }).
  // A missing or zero entry renders no count pill.
  modeCounts: { type: Object, default: () => ({}) },
  reloading: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  lastReloadedAt: { type: Object, default: null },
  // A reload that failed AFTER the session loaded. The spans on screen are
  // still real, just frozen at `lastReloadedAt`, so this degrades the reload
  // control rather than taking the pane.
  reloadFailed: { type: Boolean, default: false },
  reloadErrorDetail: { type: String, default: '' },
  hasTurns: { type: Boolean, default: false },
  snapshotStaleAt: { type: [String, null], default: null },
  workflowParentTo: { type: [Object, null], default: null },
  // Collapsed compact-row state, owned (and scroll-driven) by the parent.
  collapsed: { type: Boolean, default: false },
  // [{ key, value, label, tone }] — the mono digest on the compact row.
  digest: { type: Array, default: () => [] },
})

defineEmits(['update:viewMode', 'reload', 'jump-to-task', 'toggle-collapse'])

// Presentational toggle state — lives with the header, not the data model.
const sessionTitleExpanded = ref(false)

// Long titles wrap the h1 — keep the visible string under control so the
// page header stays compact. Tooltip on the h1 shows the full text.
const SESSION_TITLE_MAX = 90
const SESSION_TITLE_PROMPT_MAX = 72
const sessionTitleRaw = computed(() => (
  (props.session?.title || '').replace(/\s+/g, ' ').trim()
))
const sessionTitleNeedsExpand = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return false
  const max = props.session?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.length > max
})
const sessionTitle = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return 'Session timeline'
  if (sessionTitleExpanded.value || !sessionTitleNeedsExpand.value) return t
  const max = props.session?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.slice(0, max) + '…'
})

// The pulsing pill is the one piece of session state that has to be legible
// without reading a number, so it sits on the title line rather than in the
// vitals. The rule itself is shared (utils/sessionActivity) with the session
// list, the cards and the live picker: `ended_at` alone said Live forever for
// a session whose SessionEnd hook never fired.
const statusClock = computed(() => (
  props.session?.server_now && props.session?.server_now_at
    ? {
      local: props.session.server_now,
      utc: props.session.server_now_utc || props.session.server_now,
      atMs: props.session.server_now_at,
    }
    : null
))
const liveness = computed(() => sessionLivenessWithClock(props.session, statusClock.value))
// Only the *presentation* is local — these are Tailwind utility classes, not
// the CSS-var tokens the rail uses. The words come from the shared vocabulary
// so the pill, the sessions list and the ⌘J palette cannot call one session
// three things; Title Case is this chip's house style, not a fifth word.
//
// Note the dots: `inactive` is slate, not amber. Silence is benign — spending
// the attention colour on it leaves nothing left for a session that actually
// wants you.
const STATUS_PRESENTATION = {
  working: {
    dot: 'bg-emerald-500 animate-pulse',
    pill: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    title: 'spans are still arriving',
  },
  // Amber, still pulsing: the turn is open but the API call failed and the
  // agent is waiting to retry — the one state a green dot misreports as fine.
  retrying: {
    dot: 'bg-amber-500 animate-pulse',
    pill: 'border-amber-200 bg-amber-50 text-amber-700',
    title: 'the API call failed — the agent is waiting to retry',
  },
  // Same green as `working`, no pulse: equally alive, not mid-turn.
  idle: {
    dot: 'bg-emerald-500',
    pill: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    title: 'regin is holding this run and it is waiting for your prompt',
  },
  inactive: {
    dot: 'bg-slate-400',
    pill: 'border-slate-200 bg-slate-50 text-slate-500',
    title: 'no SessionEnd was recorded, but nothing has been ingested for '
      + 'over 10 minutes — this session is probably gone',
  },
  ended: {
    dot: 'bg-slate-400',
    pill: 'border-slate-200 bg-slate-50 text-slate-500',
    title: 'this session has ended',
  },
  closed: {
    dot: 'bg-slate-400',
    pill: 'border-slate-200 bg-slate-50 text-slate-500',
    title: 'this session was closed manually',
  },
}
const statusKey = computed(() => statusOfRow(props.session, liveness.value))
const status = computed(() => {
  const p = STATUS_PRESENTATION[statusKey.value] || STATUS_PRESENTATION.ended
  const l = labelOf(statusKey.value)
  return { ...p, label: l.charAt(0).toUpperCase() + l.slice(1) }
})
function titleSourceLabel(src) {
  if (src === 'claude_ai_title') return 'auto'
  if (src === 'user_rename') return 'renamed'
  if (src === 'first_prompt') return 'prompt'
  if (src === 'workflow_name') return 'workflow'
  if (src === 'user') return 'user'
  return src
}
function titleSourceTooltip(src) {
  if (src === 'claude_ai_title') return 'Auto-generated by Claude (the `ai-title` line in the transcript). Updated when the topic pivots.'
  if (src === 'user_rename') return 'You renamed this session in Claude (the `/rename` command writes a `custom-title` line). Sticky against Claude’s auto-titles.'
  if (src === 'first_prompt') return 'Derived from the first user prompt — Claude has not posted an ai-title yet.'
  if (src === 'workflow_name') return 'The workflow’s name (`meta.name` from its script) — the canonical identifier for a dynamic-workflow run. Its objective is shown as the opening bubble.'
  if (src === 'user') return 'Manually set via the regin API; not overwritten by Claude.'
  return src
}
</script>

<template>
  <header
    class="flex justify-between gap-4 flex-wrap"
    :class="collapsed ? 'items-center' : 'items-start mb-5'"
  >
    <!-- Keyed by fold state: the two layouts share no DOM, so only a fresh
         subtree can restart the enter fade; the height glide across the swap
         is the parent view's useFoldTransition tween. -->
    <!-- basis-96 is what makes the header's flex-wrap fire: a wrapping flex
         container lines items up by their BASIS, so a basis-0 `flex-1` title
         never overflows the line and instead shrinks — to ~40px at 1024px,
         wrapping the title one character per line. The floor drops the
         actions onto their own row there, and is low enough that ≥xl still
         lays out on one row. It has to clear (available width − the actions'
         intrinsic width) at 1024px: the mode menu made that column ~270px
         narrower than the segmented strip did, and at basis-80 the two fit
         one line again, squeezing the title to 184px. The compact row needs
         it too: its digest span is `lg:shrink-0` (a percentage silently
         dropped mid-number reads worse than a shrunk title), so a basis-0
         left column let the actions row (view switcher, Full/Condensed/
         Focus, Details, Reload) squeeze it to ~180px, clipping everything
         in the digest past "181 spans" — duration, tokens, cost and ctx%
         gone with no ellipsis to show they were cut. The same floor here
         drops the actions onto their own row instead.
         `session-header-collapse.spec.js` pins the floor. -->
    <div
      :key="collapsed ? 'compact' : 'full'"
      class="min-w-0 flex-1 trace-fold-enter basis-96"
    >
      <!-- Compact identity row: status dot + title + mono digest. Everything
           else (eyebrow, title row, meta chips, expanded lists) only renders
           in the full state. -->
      <!-- Wraps at every width, not just below lg. The digest is the whole
           point of the collapsed header — session status at a glance while
           scrolling a long transcript — and `shrink-0` inside an
           `overflow-hidden` row means a squeezed row silently loses the
           tail stats: at 390px it ran to x=559 against a pane ending at
           382, clipped with no indication that 177px of it was there. A
           wide actions row (view switcher, Full/Condensed/Focus, Details,
           Reload) can squeeze this column just as narrow at ≥lg — an
           `lg:flex-nowrap` here clipped duration/tokens/cost/ctx% behind
           "181 spans ·" with nothing to show they were cut. Two short
           lines beat four missing numbers, at any width. -->
      <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 overflow-hidden" v-if="collapsed">
        <router-link
          to="/trace/sessions"
          class="-ml-1 shrink-0 inline-flex items-center rounded p-0.5 text-slate-400 no-underline hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Back to the session list"
          title="Back to the session list"
        ><Icon name="chevron-left" size="14" /></router-link>
        <span
          class="h-1.5 w-1.5 shrink-0 rounded-full"
          :class="status.dot"
          :title="status.title"
        ></span>
        <!-- Still the page's h1: collapsed is the normal reading state at ≥lg,
             and swapping it for a span leaves heading navigation with no
             page heading for most of the session. -->
        <h1
          class="min-w-0 truncate text-sm font-semibold text-slate-800"
          :title="session.title || ''"
        >{{ sessionTitle }}</h1>
        <TraceProjectChip compact :session="session" />
        <span
          class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 overflow-hidden font-mono text-[11px] text-slate-500 lg:shrink-0 lg:flex-nowrap"
          data-testid="trace-header-digest"
        >
          <span
            v-for="d in digest"
            :key="d.key"
            class="flex items-baseline gap-1 whitespace-nowrap"
          >
            <span class="text-slate-300">·</span>
            <span class="font-semibold tabular-nums" :class="d.tone">{{ d.value }}</span>
            <span v-if="d.label" class="text-slate-400">{{ d.label }}</span>
          </span>
        </span>
      </div>
      <template v-else>
      <!-- The way back takes the eyebrow's slot rather than a line of its own,
           and rather than the title row: sharing that row indented the h1 90px
           past the eyebrow, the chip row and the id row, leaving the title the
           only ragged line in the header. `-ml-[5px]` cancels the lead inside
           the chevron's own box (its path starts at x=9 of a 24 viewBox, i.e.
           5.25px in at size 14), so the glyph — not the box — sits on the
           margin every other line starts from.
           "Observability · Session Trace" is what it replaces: the sidebar and
           this link both already say where you are, and it was the one line
           here carrying nothing about THIS session. -->
      <router-link
        to="/trace/sessions"
        class="-ml-[5px] mb-1 inline-flex items-center gap-1 text-sm font-semibold text-slate-500 no-underline hover:text-slate-800 focus-visible:outline-2 focus-visible:outline-blue-500"
        title="Back to the session list"
      ><Icon name="chevron-left" size="14" />Sessions</router-link>
      <!-- items-start + a nudge, NOT items-baseline: the pill is a bordered
           box whose padding hangs below its text baseline, so baseline
           alignment drops it ~6px under the title's optical centre. The nudge
           is (title line-height 30px - pill height 22.5px) / 2 = 3.75px, i.e.
           `mt-1` — it centres the pill on the title's FIRST line, which is
           what a wrapping title needs. `session-header-live-pill.spec.js`
           pins the result. -->
      <div class="flex items-start gap-2.5">
        <!-- The h1 is `min-w-0` but NOT `flex-1`: grow made the title box span
             the whole column whatever the title's length, so the shrink-0 pill
             right-aligned to the column edge and floated ~160px clear of the
             text it labels (measured at 1680px). Basis-auto sizes the h1 to its
             content up to the available width, so the pill hugs the title on a
             short one and still lands at the end of the first line on a long
             one. `session-header-live-pill.spec.js` pins the vertical half of
             this. -->
        <h1
          class="text-2xl font-semibold text-slate-900 leading-tight m-0 break-words min-w-0"
          :title="session.title || ''"
        >{{ sessionTitle }}<span
          v-if="session.title && session.title_source"
          class="ml-2 align-middle inline-block text-slate-400 text-[10px] font-medium px-1 uppercase tracking-wide"
          :title="titleSourceTooltip(session.title_source)"
        >{{ titleSourceLabel(session.title_source) }}</span></h1>
        <span
          data-testid="session-status-pill"
          class="mt-1 shrink-0 inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-0.5 text-[11px] font-semibold"
          :class="status.pill"
          :title="status.title"
        >
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="status.dot"
          ></span>{{ status.label }}
        </span>
      </div>
      <div
        v-if="sessionTitleNeedsExpand"
        class="mt-1.5"
      >
        <Button
          variant="link"
          size="sm"
          @click="sessionTitleExpanded = !sessionTitleExpanded"
        >
          {{ sessionTitleExpanded ? 'Collapse title' : 'Show full title' }}
        </Button>
      </div>
      <SessionHeaderFacts :session="session" />
      <SessionHeaderPivots
        :session="session"
        :plans="plans"
        :workflow-runs="workflowRuns"
        :snapshot-stale-at="snapshotStaleAt"
        :workflow-parent-to="workflowParentTo"
        @jump-to-task="$emit('jump-to-task', $event)"
      />
      <SessionAttribution
        v-if="session.trace_id"
        class="mt-1.5"
        :trace-id="session.trace_id"
      />
      <RefBacklinks
        v-if="session.trace_id"
        class="mt-1.5"
        :target="`session:${session.trace_id}`"
        compact
        testid="session-backlinks"
      />
      </template>
    </div>
    <!-- Below lg this column wraps to the full width of the header, so
         right-aligning it leaves every control hugging the right edge over a
         column of dead space — three ragged rows on a phone. Alignment is a
         property of the two-column layout, which only exists from lg up. -->
    <div
      data-testid="trace-header-actions"
      class="flex min-w-0 max-w-full"
      :class="collapsed
        ? 'flex-row flex-wrap items-center gap-x-3 gap-y-1.5'
        : 'flex-col items-stretch lg:items-end gap-1.5'"
    >
      <div class="flex flex-wrap justify-start lg:justify-end items-center gap-1.5">
        <!-- Header-row actions the parent owns (e.g. the agents popover).
             These survive the fold: the roster's running-count badge is live
             session status, not a detail, and folding it away hid it during
             the exact stretch — scrolled into a long transcript — when the
             reader most needs to see whether subagents are still working. -->
        <slot name="actions"></slot>
        <!-- Below lg the compact sticky strip owns the switcher; a second
             visible copy here would double every control. -->
        <TraceViewModeMenu
          class="hidden lg:inline-flex"
          :modes="MODE_OPTIONS"
          :view-mode="viewMode"
          :counts="modeCounts"
          @update:view-mode="$emit('update:viewMode', $event)"
        />
        <!-- Reading-density control for the conversation feed. Conversation
             only: the other views render no spine for a density to act on. -->
        <TraceDetailControl v-if="viewMode === 'conversation'" />
        <!-- Manual override for the collapse: pins the choice until the body
             scroll returns to the top (the parent owns that state; H is the
             keyboard twin). -->
        <Button
          variant="ghost"
          size="sm"
          class="h-auto shrink-0 gap-1 px-2 py-1 text-[11px] font-semibold text-slate-500 hover:text-slate-700"
          :title="(collapsed ? 'Show session details' : 'Collapse header for more transcript') + ' (H)'"
          :aria-expanded="!collapsed"
          data-testid="header-details-toggle"
          @click="$emit('toggle-collapse')"
        ><span
          aria-hidden="true"
          class="inline-block leading-none transition-transform"
          :class="collapsed ? 'rotate-180' : ''"
        >⌃</span>{{ collapsed ? 'Details' : 'Hide details' }}</Button>
      </div>
      <div class="flex flex-wrap items-center justify-start lg:justify-end gap-2 text-[11px] text-slate-400 font-mono">
        <!-- tabular-nums: a proportional-width timestamp jitters the column
             width on every live poll, which can toggle a wrap in the row
             above and bounce everything below the header. -->
        <span v-if="lastReloadedAt" class="tabular-nums">updated {{ fmtLocalClock(lastReloadedAt.toISOString()) }}</span>
        <!-- Degraded marker, not a banner: the spans behind it are still worth
             reading. The transport detail rides the tooltip instead of the row
             because a 500 body is often one unbroken token, and rendering it
             inline would widen (or wrap) the header on every poll tick. -->
        <span
          v-if="reloadFailed"
          data-testid="trace-reload-error"
          role="status"
          class="inline-flex items-center gap-1 text-warning-strong font-sans"
          :title="reloadErrorDetail ? `Couldn’t refresh: ${reloadErrorDetail}` : 'Couldn’t refresh'"
        ><Icon name="alert-triangle" :size="12" />not updating</span>
        <Button
          variant="link"
          size="sm"
          :disabled="reloading || loading"
          :title="'Re-fetch spans' + (hasTurns ? ' and turns' : '') + ' from the server'"
          @click="$emit('reload')"
        >
          <span :class="reloading ? 'animate-spin inline-block' : 'inline-block'">↻</span>
          <!-- Label stays constant width: a "Reload"→"Reloading…" swap widened
               this button, and as a flex sibling of the metrics column that
               squeezed the metrics into an extra wrapped line — on mobile,
               scrolled to the top of a live session, the header grew/shrank
               ~15px every poll and jerked the page. The spinning icon +
               disabled state already signal the in-progress reload. -->
          Reload
        </Button>
      </div>
    </div>
  </header>
</template>
