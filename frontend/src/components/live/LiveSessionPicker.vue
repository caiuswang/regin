<script setup>
// Session-switcher list rendered inside the /live card's "sessions" bottom
// sheet. Fetches the recent-20 sessions once on mount (the sheet body is
// v-if, so it remounts fresh on every open — no need to re-fetch on prop
// change). Active sessions sort first, each group newest-last_seen-first;
// sessionLivenessWithClock/parseLocalIso are the ONE shared source for that
// rule (utils/sessionActivity.js) — do not reimplement here.
// List query uses kind=real (Playwright/test fixtures excluded); a direct
// /live/<test-session-id> deep-link still resolves via useLiveTail's own
// kind=all lookup, so switching TO a test session isn't possible but
// visiting one directly still works.
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { fmtClock, shortTraceId } from '../../utils/traceFormatters.js'
import { sessionLivenessWithClock, parseLocalIso } from '../../utils/sessionActivity.js'
import { phaseFromLiveness, phaseLabel } from '../../utils/sessionStatus.js'

const props = defineProps({
  currentId: { type: String, default: null },
})
const emit = defineEmits(['select'])

const rows = ref([])
const loading = ref(true)
const error = ref(null)
const clock = ref(null)
// trace_id → {busy, verb, elapsed_sec} for every session a claude pane is
// actually showing. One batch tmux read for the whole list (the per-row form
// would be two subprocesses each), keyed by the id the TUI prints in its own
// footer rather than by a bridge_panes row — that registry is append-only, so
// one long-lived terminal holds a row per session that ever ran in it.
const screens = ref({})

// Three states, not two. `last_seen` only moves when a hook writes a span, so
// the recency rule alone cannot tell a session mid-think from one that
// finished half an hour ago; a screen that says `busy` settles it — and a
// screen that says otherwise settles the reverse, which is why the override
// runs both ways here. Recency stays the fallback for every session with no
// readable pane.
//
// The result is a server PHASE, not an ad-hoc word: this picker used to call a
// fresh session "idle" and a ten-minute-silent one "not running", two more
// names for states the rest of regin had already named. The words now come
// from sessionStatus.js like everywhere else.
function rowPhase(row) {
  const screen = screens.value[row.trace_id]
  if (screen) return screen.busy ? 'working' : 'idle'
  return phaseFromLiveness(sessionLivenessWithClock(row, clock.value))
}

const rowActive = (row) => !['ended', 'inactive-stale'].includes(rowPhase(row))

// Reuses the header's existing dot vocabulary (style.css): pulsing green =
// working, steady green = alive at the prompt, grey = stopped. A gone-silent
// row takes the grey, not `live-status-stale`'s amber: in a list, amber is
// spent on the one row that wants something from you, and silence does not.
// (The single-session /live header keeps the amber — it is one session, so
// scarcity is not at stake there.)
const PHASE_CLASS = {
  working: 'live-status-running',
  idle: 'live-status-idle',
  'inactive-stale': 'live-status-ended',
  ended: 'live-status-ended',
}

const sortedRows = computed(() => {
  const list = [...rows.value]
  list.sort((a, b) => {
    const aActive = rowActive(a) ? 1 : 0
    const bActive = rowActive(b) ? 1 : 0
    if (aActive !== bActive) return bActive - aActive
    const at = parseLocalIso(a.last_seen)?.getTime() ?? 0
    const bt = parseLocalIso(b.last_seen)?.getTime() ?? 0
    return bt - at
  })
  return list
})

function rowTitle(row) {
  return row.title || shortTraceId(row.trace_id)
}

// Failing soft is the whole contract: the bridge is off by default and
// reaches only tmux-launched sessions, so "no screens" is the ordinary case,
// and every row falls back to the recency rule it used before.
async function loadScreens() {
  try {
    const data = await api.get('/bridge/screen-states')
    screens.value = data.states || {}
  } catch {
    screens.value = {}
  }
}

onMounted(async () => {
  try {
    const data = await api.get('/sessions?kind=real&size=20')
    rows.value = data.sessions || []
    if (data.server_now) {
      clock.value = { local: data.server_now, utc: data.server_now_utc, atMs: Date.now() }
    }
    await loadScreens()
  } catch (e) {
    error.value = e?.message || 'Failed to load sessions.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="live-picker">
    <div v-if="error" class="live-empty">{{ error }}</div>
    <div v-else-if="loading" class="live-empty">loading…</div>
    <div v-else-if="!sortedRows.length" class="live-empty">no sessions yet</div>
    <Button
      v-for="row in sortedRows"
      v-else
      :key="row.trace_id"
      variant="ghost"
      class="live-picker-row"
      :class="{ 'live-picker-row-current': row.trace_id === currentId }"
      data-testid="live-picker-row"
      :data-trace-id="row.trace_id"
      @click="emit('select', row)"
    >
      <span
        class="live-status-dot"
        :class="PHASE_CLASS[rowPhase(row)]"
        :title="phaseLabel(rowPhase(row))"
        aria-hidden="true"
      ></span>
      <span
        class="live-picker-title"
        :class="{ 'live-mono': !row.title }"
      >{{ rowTitle(row) }}</span>
      <span class="live-picker-time">{{ fmtClock(row.last_seen) }}</span>
      <Icon
        v-if="row.trace_id === currentId"
        name="check"
        :size="14"
        data-testid="live-picker-current"
      />
    </Button>
  </div>
</template>
