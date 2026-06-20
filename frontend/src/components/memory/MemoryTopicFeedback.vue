<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'
import { useClientPage } from '../../composables/useClientPage'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import PageControls from '../PageControls.vue'

// The topic-routing feedback loop: per-topic relevance verdicts stamped by
// the InjectedRelated grade aspect, the threshold-derived PROPOSAL, and the
// human gate that actually withholds a route. The fail-rate bar only proposes;
// a route is suppressed only after a human approves it here.
const summary = ref([])
const recent = ref([])
const loading = ref(false)
const busy = ref('')
const recentOpen = ref(false)

async function reload() {
  loading.value = true
  try {
    const data = await api.get('/memory/topic-feedback')
    summary.value = data.summary || []
    recent.value = data.recent || []
  } finally {
    loading.value = false
  }
}

// Client-side search + paging for both tables (the API returns bounded sets
// with no offset / `q`). Two independent pagers — the summary grid and the
// recent-injections log scroll separately.
const {
  query: sumQuery, paged: sumRows, rawCount: sumRaw, total: sumTotal,
  page: sumP, pageSize: sumSize, pageCount: sumCount,
  hasNext: sumNext, hasPrev: sumPrev, next: sumOnNext, prev: sumOnPrev,
  goto: sumGoto, setSize: sumSetSize,
} = useClientPage(summary, {
  searchText: (s) => `${s.topic_id || ''} ${s.status || ''}`,
  size: 15,
})
const {
  query: recQuery, paged: recRows, rawCount: recRaw, total: recTotal,
  page: recP, pageSize: recSize, pageCount: recCount,
  hasNext: recNext, hasPrev: recPrev, next: recOnNext, prev: recOnPrev,
  goto: recGoto, setSize: recSetSize,
} = useClientPage(recent, {
  searchText: (r) => `${r.topic_id || ''} ${r.query || ''} ${r.relevance || ''}`,
  size: 15,
})

async function decide(topicId, decision) {
  busy.value = topicId
  try {
    await api.post(`/memory/topic-feedback/${topicId}/decision`, { decision })
    await reload()
  } finally {
    busy.value = ''
  }
}

// Reward / punish a route from the prompt it actually fired on: 👎 records a
// suppressing negative exemplar for similar future queries, 👍 a protecting
// positive — the query-local complement to the global suppress/allow decision.
async function judge(injection, polarity) {
  if (!injection.query) return
  busy.value = `${injection.session_id}:${injection.topic_id}`
  try {
    await api.post('/memory/exemplars', {
      topic_id: injection.topic_id, query: injection.query, polarity,
    })
    await reload()
  } finally {
    busy.value = ''
  }
}

const STATUS = {
  proposed: { cls: 'bg-amber-100 text-amber-800', label: 'proposed' },
  suppressed: { cls: 'bg-red-100 text-red-700', label: 'suppressed' },
  allowed: { cls: 'bg-emerald-100 text-emerald-700', label: 'allowed' },
  routing: { cls: 'bg-slate-100 text-slate-500', label: 'routing' },
}
function statusMeta(s) {
  return STATUS[s] || STATUS.routing
}

const VERDICT = {
  fail: 'bg-red-100 text-red-700',
  satisfied: 'bg-emerald-100 text-emerald-700',
  needs_revision: 'bg-amber-100 text-amber-800',
}
function verdictCls(v) {
  return VERDICT[v] || 'bg-slate-100 text-slate-500'
}
// Fail-rate bar colour scales with severity; zero metrics drop to a muted grey
// so a wall of unscored topics doesn't compete with the rows that carry signal.
function rateBar(rate) {
  if (rate >= 0.5) return 'bg-red-400'
  if (rate >= 0.2) return 'bg-amber-400'
  return 'bg-slate-300'
}
function numCls(n) {
  return n ? 'text-slate-600' : 'text-slate-300'
}
function shortId(id) {
  return (id || '').slice(0, 8)
}
function when(ts) {
  return (ts || '').slice(0, 19).replace('T', ' ')
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <section>
    <div class="flex items-baseline gap-2 mb-1">
      <h2 class="text-sm font-semibold text-slate-800">Topic routing feedback</h2>
      <span class="text-[11px] text-slate-400 font-mono">{{ summary.length }} topics</span>
      <input
        v-if="sumRaw > 0"
        v-model="sumQuery"
        type="search"
        placeholder="Filter topics…"
        class="ml-auto w-44 text-xs border border-slate-200 rounded-md px-2.5 py-1 focus-visible:outline-2 focus-visible:outline-blue-500"
      />
    </div>
    <p class="text-xs text-slate-500 mb-3 leading-relaxed">
      The fail-rate bar only proposes a route for suppression — you approve what actually gets withheld.
    </p>

    <div v-if="summary.length" class="rounded-lg border border-slate-200 bg-white overflow-hidden mb-3">
      <table class="w-full text-sm">
        <thead class="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
          <tr>
            <th class="text-left font-medium px-3 py-2">Topic</th>
            <th class="text-right font-medium px-3 py-2">Scored</th>
            <th class="text-right font-medium px-3 py-2">Fails</th>
            <th class="text-right font-medium px-3 py-2">Rate</th>
            <th class="text-left font-medium px-3 py-2">Status</th>
            <th class="text-right font-medium px-3 py-2">Decision</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!sumRows.length">
            <td colspan="6" class="px-3 py-6 text-center text-sm text-slate-400">No topic matches “{{ sumQuery }}”.</td>
          </tr>
          <tr v-for="s in sumRows" :key="s.topic_id" class="border-t border-slate-100">
            <td class="px-3 py-2 font-medium text-slate-800 truncate max-w-[14rem]">{{ s.topic_id }}</td>
            <td class="px-3 py-2 text-right font-mono tabular-nums" :class="numCls(s.scored)">{{ s.scored }}</td>
            <td class="px-3 py-2 text-right font-mono tabular-nums" :class="numCls(s.fails)">{{ s.fails }}</td>
            <!-- fail_rate is undefined until a topic is scored; show — rather than
                 a misleading 0.00, and only draw the bar for rows with real data. -->
            <td class="px-3 py-2">
              <div class="flex items-center justify-end gap-2">
                <template v-if="s.scored">
                  <span class="hidden sm:block h-1.5 w-16 rounded-full bg-slate-100 overflow-hidden" aria-hidden="true">
                    <span class="block h-full rounded-full" :class="rateBar(s.fail_rate)" :style="{ width: Math.round(s.fail_rate * 100) + '%' }"></span>
                  </span>
                  <span class="font-mono tabular-nums text-right w-9 text-slate-600">{{ s.fail_rate.toFixed(2) }}</span>
                </template>
                <span v-else class="font-mono tabular-nums text-right w-9 text-slate-300" title="not scored yet">—</span>
              </div>
            </td>
            <td class="px-3 py-2">
              <span
                class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                :class="statusMeta(s.status).cls"
              >{{ statusMeta(s.status).label }}</span>
            </td>
            <td class="px-3 py-2">
              <div class="flex items-center justify-end gap-1.5">
                <template v-if="s.status === 'proposed'">
                  <Button variant="secondary" size="sm" class="focus-visible:ring-2" :disabled="busy === s.topic_id" @click="decide(s.topic_id, 'suppressed')">Approve suppress</Button>
                  <Button variant="secondary" size="sm" class="focus-visible:ring-2" :disabled="busy === s.topic_id" @click="decide(s.topic_id, 'allowed')">Keep routing</Button>
                </template>
                <template v-else-if="s.status === 'routing'">
                  <Button variant="secondary" size="sm" class="focus-visible:ring-2" :disabled="busy === s.topic_id" @click="decide(s.topic_id, 'suppressed')">Suppress</Button>
                </template>
                <template v-else>
                  <Button variant="secondary" size="sm" class="focus-visible:ring-2" :disabled="busy === s.topic_id" @click="decide(s.topic_id, 'auto')">Reset</Button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <PageControls
        v-if="sumRaw > sumSize"
        :page="sumP"
        :page-count="sumCount"
        :total="sumTotal"
        :size="sumSize"
        :has-next="sumNext"
        :has-prev="sumPrev"
        :sizes="[15, 30, 60, 120]"
        @prev="sumOnPrev"
        @next="sumOnNext"
        @goto="sumGoto"
        @set-size="sumSetSize"
      />
    </div>
    <p v-else-if="!loading" class="text-sm text-slate-400 mb-3">No topics have been scored yet.</p>

    <!-- Accordion card: keeps the raw injection log inside the card system rather
         than floating as bare text. Full-width header = a big click target (Fitts);
         the focus ring is inset (-outline-offset) so it reads as intentional. -->
    <details v-if="recent.length" class="rounded-lg border border-slate-200 bg-white overflow-hidden" @toggle="recentOpen = $event.target.open">
      <summary class="flex items-center gap-2 cursor-pointer select-none px-3 py-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 list-none [&::-webkit-details-marker]:hidden focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-500">
        <Icon :name="recentOpen ? 'chevron-down' : 'chevron-right'" :size="14" class="text-slate-400" />
        Recent injections
        <span class="ml-auto text-[11px] font-mono text-slate-400">{{ recent.length }}</span>
      </summary>
      <div class="border-t border-slate-200">
        <div class="flex items-start gap-3 px-3 py-2.5 border-b border-slate-100">
          <p class="text-xs text-slate-500 leading-relaxed">
            Each row is one <code class="text-[11px]">&lt;topic_context&gt;</code> block regin routed into a session, with the
            prompt that triggered it. Open the session to see it in context; the Judge buttons protect or suppress this
            topic for similar future queries.
          </p>
          <input
            v-if="recRaw > 0"
            v-model="recQuery"
            type="search"
            placeholder="Filter injections…"
            class="shrink-0 w-44 text-xs border border-slate-200 rounded-md px-2.5 py-1 focus-visible:outline-2 focus-visible:outline-blue-500"
          />
        </div>
        <table class="w-full text-sm table-fixed">
          <thead class="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
            <tr>
              <th class="text-left font-medium px-3 py-2 w-24">Relevance</th>
              <th class="text-left font-medium px-3 py-2 w-48">Topic</th>
              <th class="text-left font-medium px-3 py-2">Query</th>
              <th class="text-left font-medium px-3 py-2 w-20">Session</th>
              <!-- w-40: anything narrower collides the 19-char timestamp with the Judge thumbs. -->
              <th class="text-right font-medium px-3 py-2 w-40">When</th>
              <th class="text-right font-medium px-3 py-2 w-16">Judge</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!recRows.length">
              <td colspan="6" class="px-3 py-6 text-center text-sm text-slate-400">No injection matches “{{ recQuery }}”.</td>
            </tr>
            <tr v-for="r in recRows" :key="r.session_id + r.topic_id" class="border-t border-slate-100 hover:bg-slate-50/60">
              <!-- Most rows are unscored; render that as muted text, not a badge,
                   so the rows carrying a real verdict are the ones that stand out. -->
              <td class="px-3 py-2">
                <span
                  v-if="r.relevance"
                  class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                  :class="verdictCls(r.relevance)"
                >{{ r.relevance }}</span>
                <span v-else class="text-[11px] text-slate-300">unscored</span>
              </td>
              <td class="px-3 py-2">
                <div class="font-medium text-slate-700 truncate" :title="r.topic_id">{{ r.topic_id }}</div>
              </td>
              <td class="px-3 py-2">
                <div class="text-slate-500 truncate" :title="r.query">{{ r.query ? `“${r.query}”` : '—' }}</div>
              </td>
              <td class="px-3 py-2">
                <router-link
                  v-if="r.session_id"
                  :to="`/trace/sessions/${r.session_id}`"
                  class="inline-flex items-center gap-1 font-mono text-[11px] text-slate-500 hover:text-blue-600 no-underline rounded focus-visible:outline-2 focus-visible:outline-blue-500"
                  :title="`Open session ${r.session_id} in the trace view`"
                ><Icon name="arrow-up-right" :size="12" />{{ shortId(r.session_id) }}</router-link>
                <span v-else class="font-mono text-[11px] text-slate-300">—</span>
              </td>
              <td class="px-3 py-2 text-right font-mono text-[11px] text-slate-400 whitespace-nowrap">{{ when(r.injected_at) }}</td>
              <!-- Reward / punish this actual route, keyed on its recorded prompt. -->
              <td class="px-3 py-2">
                <div class="flex items-center justify-end gap-0.5">
                  <Button variant="ghost" size="sm" class="px-1 h-auto text-slate-400 hover:text-emerald-700 focus-visible:ring-2" :disabled="!r.query || busy === `${r.session_id}:${r.topic_id}`" :aria-label="r.query ? 'Protect similar queries (positive)' : 'No recorded query to judge'" :title="r.query ? 'Protect similar queries (positive)' : 'No recorded query to judge'" @click="judge(r, 'positive')"><Icon name="thumbs-up" :size="14" /></Button>
                  <Button variant="ghost" size="sm" class="px-1 h-auto text-slate-400 hover:text-red-700 focus-visible:ring-2" :disabled="!r.query || busy === `${r.session_id}:${r.topic_id}`" :aria-label="r.query ? 'Suppress similar queries (negative)' : 'No recorded query to judge'" :title="r.query ? 'Suppress similar queries (negative)' : 'No recorded query to judge'" @click="judge(r, 'negative')"><Icon name="thumbs-down" :size="14" /></Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <PageControls
          v-if="recRaw > recSize"
          :page="recP"
          :page-count="recCount"
          :total="recTotal"
          :size="recSize"
          :has-next="recNext"
          :has-prev="recPrev"
          :sizes="[15, 30, 60, 120]"
          @prev="recOnPrev"
          @next="recOnNext"
          @goto="recGoto"
          @set-size="recSetSize"
        />
      </div>
    </details>
  </section>
</template>
