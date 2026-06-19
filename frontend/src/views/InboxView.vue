<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import InboxMessageCard from '../components/InboxMessageCard.vue'
import Button from '../components/ui/Button.vue'
import { useInboxUnread } from '../composables/useInboxUnread'

const { refresh: refreshBadge } = useInboxUnread()

const messages = ref(null)   // null = not yet loaded
const unreadCount = ref(0)
const unreadOnly = ref(false)
const includeTests = ref(false)
const busy = ref(false)

const hasUnread = computed(() => unreadCount.value > 0)

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

onMounted(loadInbox)
</script>

<template>
  <div class="mx-auto w-full max-w-6xl">
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
          :disabled="!hasUnread"
          @click="markAllRead"
        >Mark all read</Button>
      </div>
    </header>

    <div class="flex flex-wrap items-center gap-2 mb-4">
      <Button
        size="sm"
        class="border transition-colors"
        :class="unreadOnly ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
        @click="toggleUnread"
      >Unread only</Button>
      <Button
        size="sm"
        class="border transition-colors"
        :class="includeTests ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
        @click="toggleTests"
      >Include test data</Button>
      <span v-if="busy" class="ml-1 text-xs text-slate-400">Loading…</span>
    </div>

    <div v-if="messages == null" class="text-slate-500 text-sm py-20 text-center">
      Loading inbox…
    </div>
    <Card v-else-if="!messages.length">
      <div class="text-slate-500 text-sm py-20 text-center">
        {{ unreadOnly ? 'No unread messages.' : 'No messages yet. Agents post here via send_to_user.' }}
      </div>
    </Card>
    <ul v-else class="grid gap-3 lg:grid-cols-2 items-start">
      <li v-for="m in messages" :key="m.id ?? m.span_id">
        <InboxMessageCard :message="m" @open="onOpen" @dismiss="onDismiss" />
      </li>
    </ul>
  </div>
</template>
