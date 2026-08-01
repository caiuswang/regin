<script setup>
// Searchable list of sessions the launch sheet may continue, backed by
// /agent-runs/resumable. That endpoint — not /sessions — because only some
// traced sessions can still be handed to `--resume`, and a list that offers the
// rest turns a mistake the picker could have prevented into a run that dies on
// start (see lib/agent_sdk/resumable.py).
//
// Search is served, not filtered client-side: the endpoint's `q` runs in SQL
// before its page cap, so typing reaches sessions far older than the first
// screenful — which is the point of searching rather than scrolling.
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import { fmtClock, shortTraceId } from '../../utils/traceFormatters.js'
import { resumeKindLabel } from '../../utils/resumeKind.js'

const props = defineProps({
  // The session /live is open on. Marked in the list because "continue the one
  // I'm looking at" is the common case and should not require a search.
  currentId: { type: String, default: '' },
  selectedId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'cancel'])

const rows = ref([])
const loading = ref(true)
const error = ref(null)
const query = ref('')
let timer = null

async function load() {
  loading.value = true
  error.value = null
  try {
    const q = query.value.trim()
    const data = await api.get(
      `/agent-runs/resumable?limit=30${q ? `&q=${encodeURIComponent(q)}` : ''}`)
    rows.value = data.sessions || []
  } catch (e) {
    error.value = e?.message || 'Failed to load sessions.'
  } finally {
    loading.value = false
  }
}

// Debounced so a typed word is one request rather than one per keystroke; the
// list is a server round-trip, not a local filter.
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(load, 200)
})
onBeforeUnmount(() => clearTimeout(timer))
onMounted(load)

const rowTitle = (row) => row.title || shortTraceId(row.session_id)

// The two resume shapes do different things to the trace, and an operator can
// only weigh that before picking — so it is on the row, not in a footnote.
const kindLabel = (row) => resumeKindLabel(row.kind)
</script>

<template>
  <div class="live-resume">
    <Input
      v-model="query"
      type="search"
      class="live-resume-search"
      placeholder="search title, id or path…"
      aria-label="Search resumable sessions"
      data-testid="live-resume-search"
    />

    <div v-if="error" class="live-empty" data-testid="live-resume-error">{{ error }}</div>
    <div v-else-if="loading" class="live-empty">loading…</div>
    <div v-else-if="!rows.length" class="live-empty" data-testid="live-resume-empty">
      {{ query.trim() ? 'no session matches that' : 'no resumable sessions' }}
    </div>

    <div v-else class="live-resume-list">
      <Button
        v-for="row in rows"
        :key="row.session_id"
        variant="ghost"
        class="live-picker-row live-resume-row"
        :class="{ 'live-picker-row-current': row.session_id === selectedId }"
        data-testid="live-resume-row"
        :data-session-id="row.session_id"
        @click="emit('select', row)"
      >
        <span class="live-resume-main">
          <span class="live-picker-title">{{ rowTitle(row) }}</span>
          <span class="live-resume-meta">
            <span class="live-resume-kind" :data-kind="row.kind">{{ kindLabel(row) }}</span>
            <span class="live-resume-cwd">{{ row.cwd || 'no cwd recorded' }}</span>
          </span>
        </span>
        <span class="live-picker-time">{{ fmtClock(row.last_seen) }}</span>
        <Icon
          v-if="row.session_id === currentId"
          name="check"
          :size="14"
          data-testid="live-resume-current"
        />
      </Button>
    </div>

    <Button
      variant="ghost"
      class="live-resume-cancel"
      data-testid="live-resume-cancel"
      @click="emit('cancel')"
    >Back</Button>
  </div>
</template>
