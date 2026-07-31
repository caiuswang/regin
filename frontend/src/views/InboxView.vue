<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Button from '../components/ui/Button.vue'
import Input from '../components/ui/Input.vue'
import PageControls from '../components/PageControls.vue'
import InboxMessageRow from '../components/inbox/InboxMessageRow.vue'
import InboxMessageDetail from '../components/inbox/InboxMessageDetail.vue'
import { useClientPage } from '../composables/useClientPage'
import { useInboxUnread } from '../composables/useInboxUnread'
import { useLiveDecisions } from '../composables/useLiveDecisions'
import { INBOX_TYPES } from '../constants/inboxTypes'

const { refresh: refreshBadge } = useInboxUnread()
const { refreshLive, isLiveDecision, isParked } = useLiveDecisions()

const messages = ref(null)   // null = not yet loaded
const unreadCount = ref(0)
const unreadOnly = ref(false)
const includeTests = ref(false)
const busy = ref(false)
const selectedKinds = ref(new Set())   // empty = all kinds
const selectedId = ref(null)
// Below 1200px the two panes can't coexist; the reader takes the viewport
// over and the list is what you come back to. Wide screens ignore this.
const mobileDetail = ref(false)

const hasUnread = computed(() => unreadCount.value > 0)

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

const messageKey = (m) => m.id ?? m.span_id

// Chip counts are taken from the SEARCHED set, not the raw feed: a chip
// reading "Warning 3" beside a single rendered row is a count that disagrees
// with the list it claims to filter. `searched` is the search applied without
// the kind filter, so each chip answers "how many would I get if I picked
// this one" — and the All chip matches the sum.
const searched = computed(() => {
  const q = query.value.trim().toLowerCase()
  const all = messages.value || []
  if (!q) return all
  return all.filter(m => `${m.title || ''} ${m.body || ''} ${m.session_title || ''} `
    .concat(m.agent_type || '').toLowerCase().includes(q))
})

const kindCounts = computed(() => {
  const counts = {}
  for (const m of searched.value) counts[m.msg_type] = (counts[m.msg_type] || 0) + 1
  return counts
})

// The two list sections. "Needs your decision" is scoped to messages whose
// session can still receive an answer — see useLiveDecisions for why an
// un-dismissed card is not on its own evidence of a parked agent.
const decisionMessages = computed(() => paged.value.filter(isLiveDecision))
const latestMessages = computed(() => paged.value.filter(m => !isLiveDecision(m)))

// The header pill counts the WHOLE feed, not the page: at 200 rows and a page
// size of 24, deriving it from `paged` hid a genuinely parked agent on page 2
// behind no pill at all, and any search term erased it. The in-list section
// stays page-scoped (it labels the rows actually rendered), so the two can
// legitimately differ — `offPageDecisions` is what says so out loud.
const decisionCount = computed(
  () => (messages.value || []).filter(isLiveDecision).length)
const offPageDecisions = computed(
  () => decisionCount.value - decisionMessages.value.length)

const selectedMessage = computed(
  () => paged.value.find(m => messageKey(m) === selectedId.value) || null)

// Selection follows the visible page: filtering, searching, or paging away
// from the open message must not leave a detail pane showing a row that is
// no longer in the list.
watch(paged, (rows) => {
  if (!rows.length) { selectedId.value = null; return }
  if (rows.some(m => messageKey(m) === selectedId.value)) return
  selectedId.value = messageKey(rows[0])
}, { immediate: true })

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

// Reveal the parked agents the nudge is pointing at. Clearing the filters is
// only half of it: when a card is off-page purely because of pagination,
// resetting to page 0 changes nothing and the button reads as broken. So
// after clearing, page to where the first parked card actually sits.
async function showDecisionsOnly() {
  selectedKinds.value = new Set()
  query.value = ''
  goto(0)
  await nextTick()
  const idx = (messages.value || []).findIndex(isLiveDecision)
  if (idx > 0) goto(Math.floor(idx / pageSize.value))
}

async function markAllRead() {
  if (!hasUnread.value) return
  // Mark the whole inbox read server-side, not just the loaded page: the
  // feed holds only the newest `limit` rows, so older-dated unread messages
  // sit outside the window and a page-scoped mark would leave the badge
  // stuck above zero.
  await api.post('/agent-messages/read-all', { include_tests: includeTests.value })
  await Promise.all([loadInbox(), refreshBadge()])
}

// Up/Down walk the list without leaving the keyboard. Rows are real buttons,
// so Tab and Enter already work; this is the traversal a long triage list
// needs. Focus moves with the selection so the reader follows.
function onNavigate({ el, step }) {
  const rows = [...document.querySelectorAll('[data-testid="inbox-row"]')]
  const next = rows[rows.indexOf(el) + step]
  if (next) { next.focus(); next.click() }
}

function onSelect(message) {
  selectedId.value = messageKey(message)
  mobileDetail.value = true
  // Reading a parked-agent card is not handling it — the agent is still
  // waiting. Auto-marking it read on select would erase the very state the
  // decision section is reporting (and silently drop it out of the section
  // the moment you clicked it). Those cards clear only on an explicit
  // "Mark read"/Dismiss, so the count keeps nagging until you act.
  if (!isLiveDecision(message)) onRead(message)
}

async function onOpen(message) {
  // Following a link out of the inbox reads the message; navigation proceeds
  // via the router-link itself.
  if (!message.read_at && typeof message.id === 'number') {
    await api.post('/agent-messages/read', { ids: [message.id] })
    refreshBadge()
  }
}

async function onRead(message) {
  // Stamp read_at *before* the await so a fast double-click re-enters and
  // trips the guard above instead of decrementing the count twice (the
  // server's mark_read is idempotent, but the local count isn't). Update in
  // place — no reload — so the unread dot clears; re-sync if the request fails.
  if (message.read_at || typeof message.id !== 'number') return
  message.read_at = new Date().toISOString()
  unreadCount.value = Math.max(0, unreadCount.value - 1)
  // Deliberately NOT dropped from the list under "unread only": reading a
  // message is what marks it read, so evicting it on the spot pulled the row
  // out from under the reader and showed you the *next* message's body. The
  // row stays (its dot clears) until the next explicit refresh.
  try {
    await api.post('/agent-messages/read', { ids: [message.id] })
    refreshBadge()
  } catch {
    await loadInbox()
  }
}

async function onDismiss(message) {
  if (typeof message.id !== 'number') return
  await api.post(`/agent-messages/${message.id}/dismiss`)
  messages.value = (messages.value || []).filter(m => m.id !== message.id)
  mobileDetail.value = false
  refreshBadge()
}

const loadedCount = computed(() => (messages.value || []).length)
const isFiltered = computed(() => selectedKinds.value.size > 0 || query.value.trim().length > 0)

onMounted(() => Promise.all([loadInbox(), refreshLive()]))
</script>

<template>
  <div class="inbox-page">
    <header class="page-header inbox-header" :class="{ 'inbox-header-hidden': mobileDetail }">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability</div>
        <h1 class="page-title">
          Inbox
          <span
            v-if="hasUnread"
            class="text-xs font-semibold bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full"
          >{{ unreadCount }} unread</span>
          <span
            v-if="decisionCount"
            class="text-xs font-semibold bg-red-50 text-red-700 border border-red-200 px-2 py-0.5 rounded-full"
          >{{ decisionCount }} need{{ decisionCount === 1 ? 's' : '' }} a decision</span>
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
    <Card class="inbox-toolbar p-0 overflow-hidden" :class="{ 'inbox-header-hidden': mobileDetail }">
      <div class="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-slate-100">
        <div class="relative flex-1 min-w-[12rem]">
          <svg
            class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
          ><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
          <!-- `.input` is unlayered, so its `padding` shorthand beats a bare
               `pl-8`; the `!` suffix is what keeps the text clear of the
               search icon. -->
          <Input
            v-model="query"
            type="search"
            placeholder="Search title, body, session…"
            aria-label="Search messages"
            class="w-full text-sm pl-8! focus-visible:outline-2 focus-visible:outline-blue-500"
          />
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

      <div class="inbox-chips flex flex-wrap items-center gap-1.5 px-4 py-2.5">
        <Button
          size="sm"
          class="gap-1.5 border rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="selectedKinds.size === 0 ? 'bg-slate-800 border-slate-800 text-white hover:bg-slate-800 hover:text-white' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'"
          @click="clearKinds"
        >
          All
          <span class="font-mono tabular-nums opacity-70">{{ searched.length }}</span>
        </Button>
        <Button
          v-for="k in INBOX_TYPES"
          :key="k.type"
          size="sm"
          data-testid="inbox-kind-chip"
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

    <div v-if="messages == null" class="inbox-placeholder">Loading inbox…</div>
    <Card v-else-if="!loadedCount" class="inbox-placeholder-card">
      <div class="inbox-placeholder">
        {{ unreadOnly ? 'No unread messages.' : 'No messages yet. Agents post here via send_to_user.' }}
      </div>
    </Card>
    <Card v-else-if="!total" class="inbox-placeholder-card">
      <div class="inbox-placeholder">
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

    <div v-else class="inbox-panes">
      <section
        class="inbox-pane inbox-list"
        :class="{ 'inbox-pane-hidden': mobileDetail }"
        aria-label="Messages"
      >
        <div class="inbox-scroll">
          <div v-if="offPageDecisions > 0" class="inbox-offpage">
            <span class="inbox-offpage-dot" aria-hidden="true"></span>
            {{ offPageDecisions }} more
            {{ offPageDecisions === 1 ? 'agent is' : 'agents are' }} waiting outside this
            {{ isFiltered ? 'filter' : 'page' }}.
            <Button
              variant="link"
              size="sm"
              class="font-medium focus-visible:outline-2 focus-visible:outline-blue-500"
              @click="showDecisionsOnly"
            >Show them</Button>
          </div>

          <template v-if="decisionMessages.length">
            <div class="inbox-section">
              <span class="inbox-section-label inbox-section-urgent">Needs your decision</span>
              <span class="inbox-section-rule"></span>
              <span class="inbox-section-count">{{ decisionMessages.length }}</span>
            </div>
            <InboxMessageRow
              v-for="m in decisionMessages"
              :key="messageKey(m)"
              :message="m"
              :selected="messageKey(m) === selectedId"
              needs-decision
              @select="onSelect"
              @navigate="onNavigate"
            />
          </template>

          <div class="inbox-section">
            <span class="inbox-section-label">{{ decisionMessages.length ? 'Latest' : 'Messages' }}</span>
            <span class="inbox-section-rule"></span>
            <span class="inbox-section-count">{{ latestMessages.length }}</span>
          </div>
          <InboxMessageRow
            v-for="m in latestMessages"
            :key="messageKey(m)"
            :message="m"
            :selected="messageKey(m) === selectedId"
            @select="onSelect"
            @navigate="onNavigate"
          />
        </div>

        <PageControls
          v-if="total > pageSize"
          :page="page"
          :page-count="pageCount"
          :total="total"
          :size="pageSize"
          :has-next="hasNext"
          :has-prev="hasPrev"
          :sizes="[12, 24, 48, 96]"
          class="inbox-pager"
          @prev="prev"
          @next="next"
          @goto="goto"
          @set-size="setSize"
        />
      </section>

      <section
        class="inbox-pane inbox-detail-pane"
        :class="{ 'inbox-pane-hidden': !mobileDetail }"
        aria-label="Message"
      >
        <InboxMessageDetail
          :message="selectedMessage"
          :needs-decision="!!selectedMessage && isParked(selectedMessage)"
          :show-back="mobileDetail"
          @read="onRead"
          @dismiss="onDismiss"
          @open="onOpen"
          @back="mobileDetail = false"
        />
      </section>
    </div>
  </div>
</template>

<style scoped>
.inbox-toolbar { flex-shrink: 0; }

/* Eight chips wrap to four lines on a phone and push the list below the
   fold. One horizontally-scrollable strip keeps every filter reachable for
   ~40px of height. Scrolls inside itself — the page must not move sideways. */
@media (max-width: 767px) {
    .inbox-chips {
        flex-wrap: nowrap;
        overflow-x: auto;
        overscroll-behavior-x: contain;
        scrollbar-width: none;
    }
    .inbox-chips::-webkit-scrollbar { display: none; }
    .inbox-chips > * { flex-shrink: 0; }
}
.inbox-placeholder {
    padding: 5rem 1rem;
    text-align: center;
    font-size: 0.875rem;
    color: var(--color-fg-subtle);
}
.inbox-placeholder-card { flex-shrink: 0; }
.inbox-pager { flex-shrink: 0; border-top: 1px solid var(--color-border-subtle); }

/* Below 1200px there is only one pane's worth of usable width, so the list
   and the reader take turns owning the viewport rather than being squeezed
   side by side. `.inbox-panes` is a single-column grid here, so hiding one
   child leaves the other filling the row. Reading also reclaims the header
   and toolbar — on a 390px screen they cost more than half the viewport.
   Both classes are inert above the split, where both panes are on screen. */
@media (max-width: 1199px) {
    .inbox-header-hidden { display: none; }
    .inbox-pane-hidden { display: none; }
}
</style>
