<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

// The topic-routing feedback loop: per-topic relevance verdicts stamped by
// the InjectedRelated grade aspect, the threshold-derived PROPOSAL, and the
// human gate that actually withholds a route. The fail-rate bar only proposes;
// a route is suppressed only after a human approves it here.
const summary = ref([])
const recent = ref([])
const loading = ref(false)
const busy = ref('')

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
  <section v-if="summary.length" class="mb-4 mt-4">
    <div class="flex items-center gap-2 mb-1">
      <h2 class="text-sm font-semibold text-slate-800">Topic routing feedback</h2>
      <span class="text-[11px] text-slate-400 font-mono">{{ summary.length }} topics</span>
    </div>
    <p class="text-xs text-slate-500 mb-3 leading-relaxed">
      The fail-rate bar only proposes a route for suppression — you approve what actually gets withheld.
    </p>

    <div class="rounded-lg border border-slate-200 bg-white overflow-hidden mb-3">
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
          <tr v-for="s in summary" :key="s.topic_id" class="border-t border-slate-100">
            <td class="px-3 py-2 font-medium text-slate-800 truncate max-w-[14rem]">{{ s.topic_id }}</td>
            <td class="px-3 py-2 text-right font-mono text-slate-500">{{ s.scored }}</td>
            <td class="px-3 py-2 text-right font-mono text-slate-500">{{ s.fails }}</td>
            <td class="px-3 py-2 text-right font-mono text-slate-500">{{ s.fail_rate.toFixed(2) }}</td>
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
    </div>

    <details v-if="recent.length" class="text-sm">
      <summary class="cursor-pointer text-slate-500 hover:text-slate-700 rounded focus-visible:outline-2 focus-visible:outline-blue-500">
        Recent injections ({{ recent.length }})
      </summary>
      <p class="mt-2 text-xs text-slate-500 leading-relaxed">
        Each row is one <code class="text-[11px]">&lt;topic_context&gt;</code> block regin routed into a session, with the
        prompt that triggered it. Open the session to see it in context; the Judge buttons protect or suppress this
        topic for similar future queries.
      </p>
      <div class="mt-2 rounded-lg border border-slate-200 bg-white overflow-hidden">
        <table class="w-full text-sm table-fixed">
          <thead class="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
            <tr>
              <th class="text-left font-medium px-3 py-2 w-24">Relevance</th>
              <th class="text-left font-medium px-3 py-2 w-48">Topic</th>
              <th class="text-left font-medium px-3 py-2">Query</th>
              <th class="text-left font-medium px-3 py-2 w-20">Session</th>
              <th class="text-right font-medium px-3 py-2 w-40">When</th>
              <th class="text-right font-medium px-3 py-2 w-16">Judge</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in recent" :key="r.session_id + r.topic_id" class="border-t border-slate-100 hover:bg-slate-50/60">
              <td class="px-3 py-2">
                <span
                  class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                  :class="verdictCls(r.relevance)"
                >{{ r.relevance || 'unscored' }}</span>
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
      </div>
    </details>
  </section>
</template>
