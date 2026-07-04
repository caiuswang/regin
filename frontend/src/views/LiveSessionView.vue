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
import LiveSheet from '../components/live/LiveSheet.vue'
import LiveSessionPicker from '../components/live/LiveSessionPicker.vue'
import LiveQaSheet from '../components/live/LiveQaSheet.vue'
import { useLiveTail } from '../composables/useLiveTail.js'
import { fmtClock, fmtDuration, terminalSpanLabel } from '../utils/traceFormatters.js'
import {
  CATEGORIES, filterSpans, countByCategory, isSignal, rowKind, isQaSpan,
  activityCopyPayload,
} from '../utils/liveRows.js'

const route = useRoute()
const router = useRouter()
const {
  sessionId, meta, spans, hasMoreOlder, earlierCount,
  loading, loadingOlder, error, ended, active, appendedSpans,
  start, stop, loadOlder, mergeSpans,
} = useLiveTail(() => route.params.id)

// Mirrored NOW-zone state (emitted by LiveNowZone) — drives the header's
// idle dot/label and suppresses the caret while idle.
const nowState = ref('response')

// ── Filters (signal tier + category + search, all in the filter sheet) ──
const showSystem = ref(false)
const filterCat = ref('all')
const filterQuery = ref('')
const filtersActive = computed(() =>
  showSystem.value || filterCat.value !== 'all' || !!filterQuery.value.trim())

// Chip counts must equal selectable rows: count over the SAME tier-filtered
// list the rows use (with system spans hidden, a raw-list count would show
// e.g. a 'thinking' chip whose selection renders an empty tail).
const tierSpans = computed(() =>
  (showSystem.value ? spans.value : spans.value.filter(isSignal)))
const visibleRows = computed(() => filterSpans(spans.value, {
  showSystem: showSystem.value,
  category: filterCat.value,
  query: filterQuery.value,
}))
const counts = computed(() => countByCategory(tierSpans.value))
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
// suppressed while idle (the agent isn't producing anything).
const caretSpanId = computed(() => {
  if (ended.value || nowState.value === 'idle') return null
  const rows = visibleRows.value
  return rows.length ? rows[rows.length - 1].span_id : null
})

// ── Header ──
const goalOpen = ref(false)
const hdScrolled = ref(false)
// idle = alive but not working: steady green dot (no pulse) + "idle".
const statusClass = computed(() => {
  if (ended.value) return 'live-status-ended'
  return nowState.value === 'idle' ? 'live-status-idle' : 'live-status-running'
})
const statusLabel = computed(() => {
  if (ended.value) return '✓ finished'
  return nowState.value === 'idle' ? 'idle' : 'running'
})
const elapsedLabel = computed(() => {
  const t0 = meta.value.started_at ? Date.parse(meta.value.started_at) : NaN
  const t1 = meta.value.last_seen ? Date.parse(meta.value.last_seen) : NaN
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return ''
  const mins = Math.max(0, Math.round((t1 - t0) / 60000))
  const dur = mins >= 60
    ? `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, '0')}m`
    : `${mins}m`
  return `${dur} · ${fmtClock(meta.value.last_seen)}`
})

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
  scrollToBottom(true)
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
const sheet = ref(null) // { kind: 'message'|'detail'|'filter', spanId? }
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

function openSheet(kind, span) {
  savedTailScroll = tailEl.value ? tailEl.value.scrollTop : 0
  sheet.value = { kind, spanId: span?.span_id }
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

const sheetTitle = computed(() => {
  if (sheetKind.value === 'filter') return 'Filter · loaded spans'
  if (sheetKind.value === 'sessions') return 'Switch session'
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
  if (sheetKind.value === 'message') return s?.attributes?.text || ''
  if (sheetKind.value === 'detail') return s ? activityCopyPayload(s) : null
  if (sheetKind.value === 'qa' && s) {
    const a = s.attributes || {}
    if (s.name === 'tool.AskUserQuestion') {
      return JSON.stringify({ questions: a.questions || [], answers: a.answers || null }, null, 2)
    }
    return a.command_preview || a.requested_permission || ''
  }
  return null
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
async function init() {
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
            :class="statusClass"
            aria-hidden="true"
          ></span>
          <span class="live-hd-status">{{ statusLabel }}</span>
          <span class="live-hd-elapsed">
            <template v-if="elapsedLabel">· {{ elapsedLabel }}</template>
            <template v-if="ended && meta.ended_reason"> · {{ meta.ended_reason }}</template>
          </span>
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
        >{{ meta.title }}</div>
      </header>

      <div ref="tailEl" class="live-tail" data-testid="live-tail">
        <Button
          v-if="hasMoreOlder"
          variant="ghost"
          class="live-fold"
          data-testid="live-fold"
          :loading="loadingOlder"
          @click="unfold"
        >⋯ {{ earlierCount.toLocaleString() }} earlier spans · tap to load</Button>

        <div v-if="error" class="live-empty">{{ error }}</div>
        <div v-else-if="loading && !spans.length" class="live-empty">loading…</div>
        <div v-else-if="!spans.length" class="live-empty">no spans yet</div>
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

      <LiveNowZone
        ref="nowZoneRef"
        :spans="spans"
        :ended="ended"
        :active="active"
        :session-id="sessionId || ''"
        :bridge-reachable="!!meta.bridge_reachable"
        :bridge-pane="meta.bridge_pane || ''"
        :server-now="meta.server_now || ''"
        :server-now-at="meta.server_now_at || 0"
        @state-change="s => (nowState = s)"
        @open-response="s => openSheet('message', s)"
        @open-question="s => openSheet('qa', s)"
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
              <span class="live-toggle-hint">({{ hiddenSystemCount }} hidden: turn, hooks, config…)</span>
            </Checkbox>
          </div>
        </template>
      </LiveSheet>
    </div>
  </div>
</template>
