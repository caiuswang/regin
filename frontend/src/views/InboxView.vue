<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import InboxMessageCard from '../components/InboxMessageCard.vue'
import Button from '../components/ui/Button.vue'
import PageControls from '../components/PageControls.vue'
import { useClientPage } from '../composables/useClientPage'
import { useInboxUnread } from '../composables/useInboxUnread'

const { refresh: refreshBadge } = useInboxUnread()

const messages = ref(null)   // null = not yet loaded
const unreadCount = ref(0)
const unreadOnly = ref(false)
const includeTests = ref(false)
const busy = ref(false)
const selectedKinds = ref(new Set())   // empty = all kinds

const hasUnread = computed(() => unreadCount.value > 0)

// Kind chips, severity-ascending. `dot`/`sel` mirror the per-type pill palette
// in InboxMessageCard so a type reads the same colour in the filter and on the
// card it filters to.
const KINDS = [
  { type: 'progress', label: 'Progress', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'note', label: 'Note', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'lesson', label: 'Lesson', dot: 'bg-violet-500', sel: 'bg-violet-50 border-violet-400 text-violet-700' },
  { type: 'result', label: 'Result', dot: 'bg-emerald-500', sel: 'bg-emerald-50 border-emerald-400 text-emerald-700' },
  { type: 'summary', label: 'Summary', dot: 'bg-indigo-500', sel: 'bg-indigo-50 border-indigo-400 text-indigo-700' },
  { type: 'warning', label: 'Warning', dot: 'bg-amber-500', sel: 'bg-amber-50 border-amber-400 text-amber-800' },
  { type: 'blocker', label: 'Blocker', dot: 'bg-red-500', sel: 'bg-red-50 border-red-400 text-red-700' },
]

// Per-kind tallies over the loaded set — drives the chip counts so a glance
// shows the mix without opening each filter.
const kindCounts = computed(() => {
  const counts = {}
  for (const m of messages.value || []) counts[m.msg_type] = (counts[m.msg_type] || 0) + 1
  return counts
})

const kindFiltered = computed(() => {
  const all = messages.value || []
  if (!selectedKinds.value.size) return all
  return all.filter(m => selectedKinds.value.has(m.msg_type))
})

// Client-side search + paging: the inbox API returns a bounded set (newest
// `limit`, no offset / `q`), so filter and slice the fetched rows in-memory.
const {
  query, paged, total, page, pageSize, pageCount, hasNext, hasPrev,
  next, prev, goto, setSize,
} = useClientPage(kindFiltered, {
  searchText: (m) => `${m.title || ''} ${m.body || ''} ${m.session_title || ''} ${m.agent_type || ''}`,
  size: 24,
})

async function loadInbox() {
  busy.value = true
  try {
    const qs = new URLSearchParams({
      unread: String(unreadOnly.value),
      include_tests: String(includeTests.value),
    })
    const data = await api.get(`/agent-messages/inbox?${qs.toString()}`)
    messages.value = data.messages
    unreadCount.value = data.unread_count
  } catch {
    messages.value = []
  } finally {
    busy.value = false
  }
}

function toggleUnread() {
  unreadOnly.value = !unreadOnly.value
  loadInbox()
}

function toggleTests() {
  includeTests.value = !includeTests.value
  loadInbox()
}

function toggleKind(type) {
  const nextSet = new Set(selectedKinds.value)
  nextSet.has(type) ? nextSet.delete(type) : nextSet.add(type)
  selectedKinds.value = nextSet
  goto(0)
}

function clearKinds() {
  selectedKinds.value = new Set()
  goto(0)
}

async function markAllRead() {
  const ids = (messages.value || [])
    .filter(m => !m.read_at && typeof m.id === 'number')
    .map(m => m.id)
  if (!ids.length) return
  await api.post('/agent-messages/read', { ids })
  await Promise.all([loadInbox(), refreshBadge()])
}

async function onOpen(message) {
  // Opening a message reads it; navigation proceeds via the router-link.
  if (!message.read_at && typeof message.id === 'number') {
    await api.post('/agent-messages/read', { ids: [message.id] })
    refreshBadge()
  }
}

async function onDismiss(message) {
  if (typeof message.id !== 'number') return
  await api.post(`/agent-messages/${message.id}/dismiss`)
  messages.value = (messages.value || []).filter(m => m.id !== message.id)
  refreshBadge()
}

const loadedCount = computed(() => (messages.value || []).length)
const isFiltered = computed(() => selectedKinds.value.size > 0 || query.value.trim().length > 0)

onMounted(loadInbox)
</script>

<template>
  <div class="mx-auto w-full">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability</div>
        <h1 class="page-title">
          Inbox
          <span
            v-if="hasUnread"
            class="text-xs font-semibold bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full"
          >{{ unreadCount }} unread</span>
        </h1>
        <p class="page-subtitle">
          Messages your agents pushed with <code class="text-[12px]">send_to_user</code>, across all sessions.
        </p>
      </div>
      <div class="page-actions">
        <Button
          variant="secondary"
          class="focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="!hasUnread"
          @click="markAllRead"
        >Mark all read</Button>
      </div>
    </header>

    <!-- Toolbar: search + scope toggles on top, kind chips below. -->
    <Card class="mb-4 p-0 overflow-hidden">
      <div class="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-100">
        <div class="relative flex-1 min-w-[12rem]">
          <svg
            class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
          ><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
          <input
            v-model="query"
            type="search"
            placeholder="Search title, body, session…"
            aria-label="Search messages"
            class="w-full text-sm border border-slate-200 rounded-md pl-8 pr-3 py-1.5 focus-visible:outline-2 focus-visible:outline-blue-500"
          >
        </div>
        <Button
          size="sm"
          class="border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="unreadOnly ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
          @click="toggleUnread"
        >Unread only</Button>
        <Button
          size="sm"
          class="border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="includeTests ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
          @click="toggleTests"
        >Include test data</Button>
        <span v-if="busy" class="text-xs text-slate-400">Loading…</span>
      </div>

      <div class="flex flex-wrap items-center gap-1.5 px-4 py-2.5">
        <Button
          size="sm"
          class="gap-1.5 border rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="selectedKinds.size === 0 ? 'bg-slate-800 border-slate-800 text-white hover:bg-slate-800 hover:text-white' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
          @click="clearKinds"
        >
          All
          <span class="font-mono tabular-nums opacity-70">{{ loadedCount }}</span>
        </Button>
        <Button
          v-for="k in KINDS"
          :key="k.type"
          size="sm"
          :disabled="!kindCounts[k.type]"
          class="gap-1.5 border rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="selectedKinds.has(k.type) ? k.sel : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
          @click="toggleKind(k.type)"
        >
          <span class="h-1.5 w-1.5 rounded-full shrink-0" :class="k.dot"></span>
          {{ k.label }}
          <span class="font-mono tabular-nums opacity-60">{{ kindCounts[k.type] || 0 }}</span>
        </Button>
      </div>
    </Card>

    <div v-if="messages == null" class="text-slate-500 text-sm py-20 text-center">
      Loading inbox…
    </div>
    <Card v-else-if="!loadedCount">
      <div class="text-slate-500 text-sm py-20 text-center">
        {{ unreadOnly ? 'No unread messages.' : 'No messages yet. Agents post here via send_to_user.' }}
      </div>
    </Card>
    <Card v-else-if="!total">
      <div class="text-slate-500 text-sm py-20 text-center">
        No messages match the current filters.
        <Button
          v-if="isFiltered"
          variant="link"
          size="sm"
          class="ml-1 text-blue-600 hover:text-blue-800 font-medium focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="clearKinds(); query = ''"
        >Clear filters</Button>
      </div>
    </Card>
    <template v-else>
      <ul class="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 items-start">
        <li v-for="m in paged" :key="m.id ?? m.span_id">
          <InboxMessageCard :message="m" @open="onOpen" @dismiss="onDismiss" />
        </li>
      </ul>
      <div v-if="total > pageSize" class="mt-3 rounded-lg border border-slate-200 bg-white overflow-hidden">
        <PageControls
          :page="page"
          :page-count="pageCount"
          :total="total"
          :size="pageSize"
          :has-next="hasNext"
          :has-prev="hasPrev"
          :sizes="[12, 24, 48, 96]"
          @prev="prev"
          @next="next"
          @goto="goto"
          @set-size="setSize"
        />
      </div>
    </template>
  </div>
</template>
