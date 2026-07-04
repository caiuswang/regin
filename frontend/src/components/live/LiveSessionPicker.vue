<script setup>
// Session-switcher list rendered inside the /live card's "sessions" bottom
// sheet. Fetches the recent-20 sessions once on mount (the sheet body is
// v-if, so it remounts fresh on every open — no need to re-fetch on prop
// change). Active sessions sort first, each group newest-last_seen-first;
// isActiveSession/parseLocalIso are the ONE shared source for that rule
// (utils/sessionActivity.js) — do not reimplement here.
// List query uses kind=real (Playwright/test fixtures excluded); a direct
// /live/<test-session-id> deep-link still resolves via useLiveTail's own
// kind=all lookup, so switching TO a test session isn't possible but
// visiting one directly still works.
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { fmtClock } from '../../utils/traceFormatters.js'
import { isActiveSession, parseLocalIso } from '../../utils/sessionActivity.js'

const props = defineProps({
  currentId: { type: String, default: null },
})
const emit = defineEmits(['select'])

const rows = ref([])
const loading = ref(true)
const error = ref(null)

const sortedRows = computed(() => {
  const list = [...rows.value]
  list.sort((a, b) => {
    const aActive = isActiveSession(a) ? 1 : 0
    const bActive = isActiveSession(b) ? 1 : 0
    if (aActive !== bActive) return bActive - aActive
    const at = parseLocalIso(a.last_seen)?.getTime() ?? 0
    const bt = parseLocalIso(b.last_seen)?.getTime() ?? 0
    return bt - at
  })
  return list
})

function rowTitle(row) {
  return row.title || (row.trace_id ? row.trace_id.slice(0, 8) : '')
}

onMounted(async () => {
  try {
    const data = await api.get('/sessions?kind=real&size=20')
    rows.value = data.sessions || []
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
        :class="isActiveSession(row) ? 'live-status-running' : 'live-status-ended'"
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
