<script setup>
// /live/:id? — mobile session-tail card. One card, four zones: header,
// fold row, tail (the ONLY scroll region), sticky NOW zone; row detail /
// full-message / filter interactions live in bottom sheets. Data + poll
// lifecycle live in useLiveTail; row semantics in utils/liveRows.js.
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import Input from '../components/ui/Input.vue'
import Checkbox from '../components/ui/Checkbox.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import LiveTailRow from '../components/live/LiveTailRow.vue'
import LiveNowZone from '../components/live/LiveNowZone.vue'
import LiveQueuedChips from '../components/live/LiveQueuedChips.vue'
import LiveSheet from '../components/live/LiveSheet.vue'
import LiveSessionPicker from '../components/live/LiveSessionPicker.vue'
import LiveQaSheet from '../components/live/LiveQaSheet.vue'
import LiveAgentSheet from '../components/live/LiveAgentSheet.vue'
import LiveAgentDetail from '../components/live/LiveAgentDetail.vue'
import LiveTaskSheet from '../components/live/LiveTaskSheet.vue'
import LiveCtxMeter from '../components/live/LiveCtxMeter.vue'
import LiveScopeBar from '../components/live/LiveScopeBar.vue'
import { useLiveTail } from '../composables/useLiveTail.js'
import { useLiveAgents } from '../composables/useLiveAgents.js'
import { useLiveScope } from '../composables/useLiveScope.js'
import { useQueuedPrompts } from '../composables/useQueuedPrompts.js'
import { fmtClock, fmtDuration, fmtModel, terminalSpanLabel } from '../utils/traceFormatters.js'
import { parseLocalIso } from '../utils/sessionActivity.js'
import {
  CATEGORIES, filterSpans, countByCategory, isSignal, rowKind, isQaSpan,
  activityCopyPayload, taskSummaryOf,
} from '../utils/liveRows.js'
import { categoryOf } from '../utils/traceFormatters.js'

const route = useRoute()
const router = useRouter()
const {
  sessionId, meta, spans, hasMoreOlder, earlierCount,
  loading, loadingOlder, error, ended, active, stale,
  connectionLost, appendedSpans,
  start, stop, loadOlder, mergeSpans,
} = useLiveTail(() => route.params.id)

// The server phase is the single state truth. The header always shows the
// MAIN agent (agent_phase.main); the NOW zone gets the scoped agent's phase
// when scoped, else main (computed inline in the template). Empty until the
// first summary lands.
const mainPhase = computed(() => meta.value.agent_phase?.main || meta.value.phase || '')

// Queued / steer prompts (server-derived + optimistic just-sent steers).
const queued = useQueuedPrompts(() => meta.value.queued_prompts, () => spans.value)

// Agent roster: the server's whole-session agent_roster is the single
// source of truth (window-independent — the loaded tail silently drops
// agents whose markers aged out); the server owns classification.
const liveAgents = useLiveAgents(() => spans.value, () => meta.value.agent_roster)
// Per-agent span scoping: scopeId re-partitions the tail to one subagent;
// the header keeps showing MAIN-session truth. Scroll save/restore on
// enter/exit stays here (this view owns the tail element) via a watch.
// loadOlder/hasMoreOlder are threaded in so the scope can auto-page an old
// subagent's spans into view on first entry — no manual fold-row tap.
const scope = useLiveScope(
  () => spans.value, () => liveAgents.agents, loadOlder, () => hasMoreOlder.value)

// ── Filters (signal tier + category + search, all in the filter sheet) ──
const showSystem = ref(false)
const filterCat = ref('all')
const filterQuery = ref('')
const filtersActive = computed(() =>
  showSystem.value || filterCat.value !== 'all' || !!filterQuery.value.trim())

// Chip counts must equal selectable rows: count over the list the rows
// actually use — same signal/system tier AND the live search query (a chip
// advertising 12 rows must not select down to an empty tail because a
// query was active). One shared tier+query pass; the category chip is a
// cheap refinement of it.
// Scope partition first: main scope drops agent-internal spans, an
// agent scope keeps only that agent's — then the usual signal/query lens.
const searchedSpans = computed(() => filterSpans(scope.scopedSpans, {
  showSystem: showSystem.value,
  category: 'all',
  query: filterQuery.value,
}))
const visibleRows = computed(() => (filterCat.value === 'all'
  ? searchedSpans.value
  : searchedSpans.value.filter(s => categoryOf(s) === filterCat.value)))
const counts = computed(() => countByCategory(searchedSpans.value))
// System-span count for the toggle label — independent of the toggle state.
const hiddenSystemCount = computed(() =>
  spans.value.filter(s => !isSignal(s)).length)

// subagent.start parents → indented child rows (same cue as the Terminal log)
const subagentIds = computed(() => {
  const ids = new Set()
  for (const s of spans.value) if (s.name === 'subagent.start') ids.add(s.span_id)
  return ids
})

// Blinking caret rides the newest visible row while the session runs —
// suppressed unless the MAIN agent is actively working/blocked (idle,
// inactive-stale, ended never blink).
const CARET_LIVE_PHASES = new Set(['working', 'waiting-permission', 'waiting-input'])
const caretSpanId = computed(() => {
  // Scoped: the caret is a MAIN-session liveness cue — suppress it in an
  // agent scope (a finished agent's frozen tail must not blink).
  if (ended.value || scope.scopeId || !CARET_LIVE_PHASES.has(mainPhase.value)) return null
  // Phase bands are structure, not content — the caret belongs on the
  // newest real row even when a band is last.
  const rows = visibleRows.value
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rowKind(rows[i]) !== 'phase') return rows[i].span_id
  }
  return null
})

// ── Header ──
const goalOpen = ref(false)
const hdScrolled = ref(false)
// Header status = the MAIN agent's server phase: { class, label }. The dot
// pulses only while working; waiting-* gets amber attention; idle a steady
// green; inactive-stale amber; ended a grey done dot. A pre-summary fallback
// keeps the header honest for the sub-second before the first phase lands.
const PHASE_STATUS = {
  ended: { cls: 'live-status-ended', label: '✓ finished' },
  'inactive-stale': { cls: 'live-status-stale', label: 'inactive' },
  idle: { cls: 'live-status-idle', label: 'idle' },
  'waiting-permission': { cls: 'live-status-waiting', label: 'waiting' },
  'waiting-input': { cls: 'live-status-waiting', label: 'waiting' },
  working: { cls: 'live-status-running', label: 'running' },
}
const headerStatus = computed(() => {
  const s = PHASE_STATUS[mainPhase.value]
  if (s) return s
  if (ended.value) return PHASE_STATUS.ended
  if (stale.value) return PHASE_STATUS['inactive-stale']
  return PHASE_STATUS.working
})
const elapsedLabel = computed(() => {
  const t0 = parseLocalIso(meta.value.started_at)?.getTime() ?? NaN
  const t1 = parseLocalIso(meta.value.last_seen)?.getTime() ?? NaN
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return ''
  const mins = Math.max(0, Math.round((t1 - t0) / 60000))
  const dur = mins >= 60
    ? `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, '0')}m`
    : `${mins}m`
  return `${dur} · ${fmtClock(meta.value.last_seen)}`
})

// ── Header tasks chip + agents button ──
// Tasks: done/total sourced ONLY from the server's final snapshot (loaded
// tail spans fold away and would make a client tally diverge).
const taskSummary = computed(() => taskSummaryOf(meta.value.task_list?.final))

// Header meta line: repo · model + the segment-aware live-peak ctx%.
const ctxPct = computed(() => {
  const p = meta.value.context_pct
  return typeof p === 'number' ? p : null
})
const metaIdLabel = computed(() => [
  meta.value.repo,
  meta.value.model ? fmtModel(meta.value.model) : '',
].filter(Boolean).join(' · '))

// ── Tail scroll: follow-tail when pinned, "N new" chip when scrolled up ──
const tailEl = ref(null)
const cardEl = ref(null)
const nowZoneRef = ref(null)
const newCount = ref(0)
const enteringIds = ref(new Set())

function isPinned() {
  const el = tailEl.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function scrollToBottom(smooth) {
  const el = tailEl.value
  if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

// Entering a scope pins the (short) scoped tail to its bottom; exiting
// restores the main tail's exact pre-scope scroll position.
let scopeReturnScroll = 0
watch(() => scope.scopeId, (id, prev) => {
  if (id && !prev) scopeReturnScroll = tailEl.value?.scrollTop ?? 0
  nextTick(() => {
    if (id) scrollToBottom(false)
    else if (tailEl.value) tailEl.value.scrollTop = scopeReturnScroll
  })
})

// Pinned-ness as of the last user scroll. The NOW zone's ResizeObserver
// cannot measure isPinned() AFTER a height change (a grown footer shrinks
// the tail and misreads a pinned user as scrolled up), so the pre-change
// answer is tracked here and consumed by onNowZoneResize.
let pinnedBeforeResize = true
let nowRo = null

function onTailScroll() {
  const el = tailEl.value
  if (!el) return
  hdScrolled.value = el.scrollTop > 4
  pinnedBeforeResize = isPinned()
  if (newCount.value && isPinned()) newCount.value = 0
}

// NOW-zone height changes (composer mount/unmount, textarea autogrow):
// keep the "N new" chip riding the zone (--live-now-h) and re-pin the tail
// if the user was pinned before the change.
function onNowZoneResize(el) {
  cardEl.value?.style.setProperty('--live-now-h', `${el.offsetHeight}px`)
  if (pinnedBeforeResize) nextTick(() => scrollToBottom(false))
}

function markEntering(rows) {
  if (!rows.length) return
  const next = new Set(enteringIds.value)
  for (const s of rows) next.add(s.span_id)
  enteringIds.value = next
  setTimeout(() => {
    const cleared = new Set(enteringIds.value)
    for (const s of rows) cleared.delete(s.span_id)
    enteringIds.value = cleared
  }, 600)
}

// Poll appends drive follow-tail + the chip; the chip counts only spans the
// user would SEE under the current filter (spec 8c).
watch(appendedSpans, async (added) => {
  const pinned = isPinned()
  const visNew = filterSpans(added, {
    showSystem: showSystem.value,
    category: filterCat.value,
    query: filterQuery.value,
  })
  if (!visNew.length) return
  markEntering(visNew)
  if (pinned) {
    await nextTick()
    scrollToBottom(true)
  } else {
    newCount.value += visNew.length
  }
})

function jumpToNew() {
  // Instant, not smooth: rows keep arriving on a live session, and a
  // mid-animation arrival reads the tail as unpinned — re-incrementing the
  // chip that was just cleared and landing short of the bottom.
  scrollToBottom(false)
  newCount.value = 0
}

// ── Fold: unfold one older page, viewport anchored across the prepend ──
async function unfold() {
  const el = tailEl.value
  const prevH = el ? el.scrollHeight : 0
  const prevTop = el ? el.scrollTop : 0
  await loadOlder()
  await nextTick()
  if (el) el.scrollTop = el.scrollHeight - prevH + prevTop
}

// ── Bottom sheets (message / detail / filter) ──
const sheet = ref(null) // { kind: 'message'|'detail'|'filter'|'agent', spanId?, agentId? }
let savedTailScroll = 0
const sheetOpen = computed({
  get: () => !!sheet.value,
  set(v) { if (!v) closeSheet() },
})
const sheetKind = computed(() => sheet.value?.kind)
// Resolved live so a lazy content fetch re-renders the open sheet.
const sheetSpan = computed(() => (sheet.value?.spanId
  ? spans.value.find(s => s.span_id === sheet.value.spanId)
  : null))

// A span-pinned sheet whose span gets pruned mid-open (placeholder resolved,
// window aged out) would linger as a blank title bar over an empty body —
// close it instead of stranding it.
watch(sheetSpan, (s) => {
  if (sheet.value?.spanId && !s) closeSheet()
})

// `payload` doubles as a span (message/detail/qa) or a roster agent entry
// (agent kind, keyed by agentId) — one function for both keeps the sheet's
// surface area from growing per kind. The agent sheet re-resolves its entry
// live off the roster (sheetTitle/sheetCopy/template), so a status change
// while open still reflects — unlike a span, an agentId never ages out.
function openSheet(kind, payload) {
  savedTailScroll = tailEl.value ? tailEl.value.scrollTop : 0
  sheet.value = { kind, spanId: payload?.span_id, agentId: payload?.agentId }
}

function closeSheet() {
  sheet.value = null
  nextTick(() => {
    if (tailEl.value) tailEl.value.scrollTop = savedTailScroll
  })
}

function onPickSession(row) {
  closeSheet()
  if (row.trace_id && row.trace_id !== sessionId.value) router.push('/live/' + row.trace_id)
}

// Sheet title/copy are dispatch tables keyed on kind (not if-ladders): the
// static-title kinds collapse to one lookup, leaving only the span-derived
// kinds to compute.
const sheetTitle = computed(() => {
  const fixed = {
    filter: 'Filter · loaded spans',
    sessions: 'Switch session',
    agents: 'Agents',
    tasks: 'Tasks',
  }[sheetKind.value]
  if (fixed) return fixed
  if (sheetKind.value === 'agent') {
    const ag = liveAgents.agents.find(a => a.agentId === sheet.value?.agentId)
    return ag ? `${ag.agentType}${ag.startClock ? ' · ' + ag.startClock : ''}` : 'Agent'
  }
  const s = sheetSpan.value
  if (!s) return ''
  if (sheetKind.value === 'qa') {
    const ask = s.name === 'tool.AskUserQuestion'
    if (ask && s.status_code === 'PENDING') return 'Ask user · waiting'
    return `${ask ? 'Ask user' : 'Permission'} · ${fmtClock(s.start_time)}`
  }
  const who = s.name === 'prompt' ? 'You' : 'Assistant'
  const label = sheetKind.value === 'message' ? who : terminalSpanLabel(s)
  return `${label} · ${fmtClock(s.start_time)}`
})

const sheetCopy = computed(() => {
  const s = sheetSpan.value
  const a = s?.attributes || {}
  // null, not '' — an empty string still renders the Copy button and flips
  // it to a misleading "✓ Copied" for nothing.
  const byKind = {
    message: () => s?.attributes?.text || '',
    detail: () => (s ? activityCopyPayload(s) : null),
    qa: () => (s?.name === 'tool.AskUserQuestion'
      ? JSON.stringify({ questions: a.questions || [], answers: a.answers || null }, null, 2)
      : (a.command_preview || a.requested_permission || null)),
    agent: () => (liveAgents.agents.find(x => x.agentId === sheet.value?.agentId)
      ?.promptPreview || null),
  }[sheetKind.value]
  return byKind ? byKind() : null
})

const detailAttrs = computed(() => {
  const a = sheetSpan.value?.attributes || {}
  return Object.entries(a).map(([k, v]) =>
    [k, typeof v === 'string' ? v : JSON.stringify(v)])
})

// Message tap → full text sheet; ask/permission tap → Q&A sheet; activity
// tap → attrs sheet, lazy-fetching full attributes for shallow rows
// (mirrors the Terminal's onRowClick).
function onRowSelect(span) {
  if (rowKind(span) === 'msg' && span.attributes?.text) {
    openSheet('message', span)
    return
  }
  if (isQaSpan(span)) {
    openSheet('qa', span)
    // Only ask spans lazy-load: a shallow ask lacks `questions`; permission
    // spans never carry them, so this guard would refetch on every tap.
    if (span.name === 'tool.AskUserQuestion'
      && !(span.attributes?.questions || []).length) fetchContent(span)
    return
  }
  openSheet('detail', span)
  if (!Object.keys(span.attributes || {}).length) fetchContent(span)
}

async function fetchContent(span) {
  const sid = sessionId.value
  try {
    const data = await api.get(
      `/sessions/${sid}/spans/${span.span_id}/content`,
    )
    // Discard if the route switched sessions while the fetch was in flight.
    if (sessionId.value !== sid) return
    mergeSpans([{ ...span, attributes: data.attributes || {} }])
  } catch { /* sheet keeps the shallow fields */ }
}

// ── Lifecycle ──
// View-local state the composable's resetState can't see — without this a
// session switch leaks the previous session's "N new" chip, an open sheet
// (blank once spans reset), the expanded goal, and the header shadow.
// Filters deliberately persist: carrying a lens across sessions is useful,
// and the header badge keeps it visible.
function resetViewState() {
  newCount.value = 0
  goalOpen.value = false
  hdScrolled.value = false
  enteringIds.value = new Set()
  sheet.value = null
  savedTailScroll = 0
  scope.exit()
}

async function init() {
  resetViewState()
  await start()
  await nextTick()
  scrollToBottom(false)
}

// Re-init only while we're still ON the live route — leaving it also
// changes route.params and would otherwise restart the poll mid-unmount.
watch(() => route.params.id, () => {
  if (route.name === 'live') init()
})
onMounted(() => {
  // Programmatic listener (not @scroll) — the tail is a scroll region, not
  // a clickable surface; rows carry their own pointer affordance.
  tailEl.value?.addEventListener('scroll', onTailScroll, { passive: true })
  const nowEl = nowZoneRef.value?.$el
  if (nowEl && typeof ResizeObserver !== 'undefined') {
    nowRo = new ResizeObserver(() => onNowZoneResize(nowEl))
    nowRo.observe(nowEl) // fires once on observe → seeds --live-now-h
  }
  init()
})
onUnmounted(() => {
  tailEl.value?.removeEventListener('scroll', onTailScroll)
  nowRo?.disconnect()
  stop()
})
</script>

<template>
  <div class="live-page">
    <div ref="cardEl" class="live-card" data-testid="live-card">
      <header
        class="live-hd"
        :class="{ 'live-hd-scrolled': hdScrolled }"
        data-testid="live-header"
      >
        <div class="live-hd-row">
          <span
            class="live-status-dot"
            :class="headerStatus.cls"
            aria-hidden="true"
          ></span>
          <span class="live-hd-status">{{ headerStatus.label }}</span>
          <span class="live-hd-elapsed">
            <template v-if="elapsedLabel">· {{ elapsedLabel }}</template>
            <template v-if="ended && meta.ended_reason"> · {{ meta.ended_reason }}</template>
          </span>
          <span
            v-if="connectionLost"
            class="live-hd-conn"
            data-testid="live-conn-lost"
          >connection lost</span>
          <span class="live-hd-spacer" aria-hidden="true"></span>
          <Button
            v-if="taskSummary"
            variant="ghost"
            class="live-hd-tasks"
            :class="{ 'live-hd-tasks-active': taskSummary.inProgress > 0 }"
            data-testid="live-tasks-chip"
            aria-label="Open task list"
            @click="openSheet('tasks')"
          >
            <Icon name="list-checks" :size="12" />
            <span class="live-tabnum">{{ taskSummary.done }}/{{ taskSummary.total }}</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            class="live-hd-agents"
            data-testid="live-agents-btn"
            aria-label="Open running agents"
            @click="openSheet('agents')"
          >
            <Icon name="workflow" :size="14" />
            <span
              v-if="liveAgents.runningCount > 0"
              class="live-agent-badge live-tabnum"
              data-testid="live-agents-badge"
            >{{ liveAgents.runningCount }}</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            class="live-hd-btn"
            data-testid="live-switch"
            aria-label="Switch session"
            @click="openSheet('sessions')"
          >
            <Icon name="list" :size="14" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            class="live-hd-btn"
            data-testid="live-filter"
            aria-label="Filter and search spans"
            @click="openSheet('filter')"
          >
            <Icon name="filter" :size="14" />
            <span v-if="filtersActive" class="live-hd-badge" aria-hidden="true"></span>
          </Button>
        </div>
        <div
          v-if="meta.title"
          class="live-goal cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ 'live-goal-open': goalOpen }"
          data-testid="live-goal"
          role="button"
          tabindex="0"
          title="Tap to expand"
          @click="goalOpen = !goalOpen"
          @keydown.enter="goalOpen = !goalOpen"
          @keydown.space.prevent="goalOpen = !goalOpen"
        >{{ meta.title }}</div>
        <div v-if="metaIdLabel || ctxPct != null" class="live-hd-meta" data-testid="live-hd-meta">
          <span class="live-hd-meta-id">{{ metaIdLabel }}</span>
          <LiveCtxMeter :pct="ctxPct" />
        </div>
      </header>

      <LiveScopeBar
        v-if="scope.scopedAgent"
        :agent="scope.scopedAgent"
        :server-now="meta.server_now || ''"
        :server-now-at="meta.server_now_at || 0"
        @exit="scope.exit"
      />

      <div ref="tailEl" class="live-tail" data-testid="live-tail">
        <!-- Paging is a main-scope concept: an agent's span set is small and
             shown whole, so the fold row is suppressed while scoped. -->
        <Button
          v-if="hasMoreOlder && !scope.scopeId"
          variant="ghost"
          class="live-fold"
          data-testid="live-fold"
          :loading="loadingOlder"
          @click="unfold"
        >⋯ {{ earlierCount.toLocaleString() }} earlier spans · tap to load</Button>

        <div v-if="error" class="live-empty">{{ error }}</div>
        <div v-else-if="loading && !spans.length" class="live-empty">loading…</div>
        <div v-else-if="!spans.length" class="live-empty">no spans yet</div>
        <!-- Auto-paging (scope entry looking for the agent's spans in older
             windows) is a LOADING state, not the terminal empty one below —
             conflating them would flash "no spans captured" for an agent
             whose spans just haven't paged in yet. -->
        <div
          v-else-if="scope.scopeId && scope.autoPaging && !visibleRows.length"
          class="live-empty"
          data-testid="live-scope-loading"
        >loading…</div>
        <!-- Scoped-empty is TERMINAL, not a spinner; the hint distinguishes
             "not loaded" (the roster says spans exist) from "never
             captured". The fold row stays hidden in scope, so the
             not-loaded state carries its own load action as a fallback —
             auto-paging normally resolves this on scope entry already. -->
        <div
          v-else-if="scope.scopeId && !visibleRows.length"
          class="live-empty"
          data-testid="live-scope-empty"
        >
          {{ scope.scopedEmptyHint }}
          <Button
            v-if="scope.scopedSpansExist"
            variant="link"
            size="sm"
            class="live-now-more"
            data-testid="live-scope-load"
            :loading="loadingOlder"
            @click="unfold"
          >load earlier history</Button>
        </div>
        <div v-else-if="!visibleRows.length" class="live-empty">
          no spans match the current filter
        </div>

        <LiveTailRow
          v-for="row in visibleRows"
          :key="row.span_id"
          :span="row"
          :sub="subagentIds.has(row.parent_id)"
          :caret="row.span_id === caretSpanId"
          :entering="enteringIds.has(row.span_id)"
          @select="onRowSelect"
        />
      </div>

      <Button
        v-if="newCount > 0"
        variant="primary"
        class="live-newchip"
        data-testid="live-newchip"
        @click="jumpToNew"
      >↓ {{ newCount }} new</Button>

      <LiveQueuedChips v-if="!scope.scopeId" :items="queued.items" />

      <LiveNowZone
        ref="nowZoneRef"
        :spans="scope.mainSpans"
        :ended="ended"
        :active="active"
        :phase="scope.scopeId ? (meta.agent_phase?.[scope.scopeId] || '') : mainPhase"
        :session-id="sessionId || ''"
        :bridge-reachable="!!meta.bridge_reachable"
        :bridge-pane="meta.bridge_pane || ''"
        :server-now="meta.server_now || ''"
        :server-now-at="meta.server_now_at || 0"
        :ctx-pct="ctxPct"
        :scope-agent="scope.scopedAgent"
        :agents-running="liveAgents.runningCount"
        :agents-waiting="liveAgents.waitingCount"
        @sent="queued.noteSent"
        @open-response="s => openSheet('message', s)"
        @open-question="s => openSheet('qa', s)"
        @exit-scope="scope.exit"
        @open-agents="openSheet('agents')"
      />

      <LiveSheet v-model:open="sheetOpen" :title="sheetTitle" :copy-payload="sheetCopy">
        <template v-if="sheetKind === 'message'">
          <MarkdownContent :markdown="sheetSpan?.attributes?.text || ''" />
        </template>

        <template v-else-if="sheetKind === 'detail'">
          <dl v-if="detailAttrs.length" class="live-attrs">
            <template v-for="[k, v] in detailAttrs" :key="k">
              <dt>{{ k }}</dt>
              <dd>{{ v }}</dd>
            </template>
            <dt>span_id</dt>
            <dd>{{ sheetSpan?.span_id }}</dd>
            <template v-if="sheetSpan?.duration_ms">
              <dt>duration</dt>
              <dd>{{ fmtDuration(sheetSpan.duration_ms) }}</dd>
            </template>
          </dl>
          <p v-else class="live-empty">Loading attributes…</p>
        </template>

        <template v-else-if="sheetKind === 'qa'">
          <LiveQaSheet
            v-if="sheetSpan"
            :span="sheetSpan"
            :session-id="sessionId || ''"
            :bridge-reachable="!!meta.bridge_reachable"
            @answered="closeSheet"
          />
        </template>

        <template v-else-if="sheetKind === 'agents'">
          <LiveAgentSheet
            :running-agents="liveAgents.runningAgents"
            :finished-agents="liveAgents.finishedAgents"
            :server-now="meta.server_now || ''"
            :server-now-at="meta.server_now_at || 0"
            @view-agent="a => openSheet('agent', a)"
            @scope="a => { scope.enter(a.agentId); closeSheet() }"
          />
        </template>

        <template v-else-if="sheetKind === 'agent'">
          <LiveAgentDetail
            :agent="liveAgents.agents.find(a => a.agentId === sheet?.agentId) || null"
            :server-now="meta.server_now || ''"
            :server-now-at="meta.server_now_at || 0"
          />
        </template>

        <template v-else-if="sheetKind === 'tasks'">
          <LiveTaskSheet :tasks="meta.task_list?.final || []" />
        </template>

        <template v-else-if="sheetKind === 'sessions'">
          <LiveSessionPicker :current-id="sessionId" @select="onPickSession" />
        </template>

        <template v-else-if="sheetKind === 'filter'">
          <Input
            v-model="filterQuery"
            type="search"
            placeholder="search spans, files, commands…"
            aria-label="Search spans"
          />
          <div class="live-chips">
            <Button
              v-for="c in CATEGORIES"
              :key="c.id"
              variant="ghost"
              size="sm"
              class="live-chip"
              :class="{ 'live-chip-on': filterCat === c.id }"
              @click="filterCat = c.id"
            >
              <span class="live-dot" :class="c.dotClass"></span>
              {{ c.label }}
              <span class="live-chip-n">{{ counts[c.id] }}</span>
            </Button>
          </div>
          <div class="live-toggle-row">
            <Checkbox v-model="showSystem" data-testid="live-toggle-system">
              Show system spans
              <span class="live-toggle-hint">
                ({{ hiddenSystemCount }} {{ showSystem ? 'shown' : 'hidden' }}: turn, hooks, config…)
              </span>
            </Checkbox>
          </div>
        </template>
      </LiveSheet>
    </div>
  </div>
</template>
