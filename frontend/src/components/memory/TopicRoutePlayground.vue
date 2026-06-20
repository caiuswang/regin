<script setup>
import { ref, computed } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Select from '../ui/Select.vue'
import Icon from '../ui/Icon.vue'
import ExemplarCaseList from './ExemplarCaseList.vue'

// Topic-route playground: type a query, see what the recall hook would route it
// to (keyword `match_topic`) and which topics' query-exemplars lean on it
// (pos/neg max-cosine + suppress verdict), then stamp 👍/👎 to build topic
// exemplar cases by hand. The write path is the shared POST /memory/exemplars
// (topic_id + query + polarity); preview is GET-shaped POST /topic-route-preview.
const query = ref('')
const result = ref(null)
const loading = ref(false)
const busy = ref('')
const pickTopic = ref('')
const expanded = ref(new Set())  // topic ids whose case list is open

function toggle(topicId) {
  const next = new Set(expanded.value)
  next.has(topicId) ? next.delete(topicId) : next.add(topicId)
  expanded.value = next
}

const topicOptions = computed(() =>
  (result.value?.topics || []).map(t => ({ value: t.id, label: t.label })))

// True once a route fired with a known basis — drives the "matched on …" line
// that explains *why* this topic won (keyword overlap), since the route is
// decided by `match_topic`, not by the pos/neg exemplar scores in the table.
const routeBasis = computed(() => !!result.value?.routed?.strategy)

async function preview() {
  if (!query.value.trim()) return
  loading.value = true
  try {
    const data = await api.post('/memory/topic-route-preview', { query: query.value.trim() })
    result.value = data.error ? null : data
    if (result.value && !pickTopic.value) {
      pickTopic.value = result.value.topics?.[0]?.id || ''
    }
  } finally {
    loading.value = false
  }
}

async function label(topicId, polarity) {
  if (!topicId) return
  busy.value = `${topicId}:${polarity}`
  try {
    await api.post('/memory/exemplars', {
      topic_id: topicId, query: result.value.query, polarity,
    })
    await preview()  // re-pull so sims/counts reflect the new case
  } finally {
    busy.value = ''
  }
}

function sim(v) {
  return (v || 0).toFixed(2)
}
function rowState(c) {
  if (c.decision === 'allowed') return { cls: 'bg-emerald-100 text-emerald-700', label: 'protected' }
  if (c.suppressed) return { cls: 'bg-red-100 text-red-700', label: 'suppressed' }
  return { cls: 'bg-slate-100 text-slate-500', label: 'routes' }
}

defineExpose({ preview })
</script>

<template>
  <section>
    <div class="flex items-baseline gap-2 mb-1">
      <h2 class="text-sm font-semibold text-slate-800">Topic-route playground</h2>
      <span v-if="result?.threshold" class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">suppress ≥ {{ sim(result.threshold) }}</span>
    </div>
    <p class="text-xs text-slate-500 mb-3 leading-relaxed">
      Probe a query to see where the recall hook routes it, then protect or suppress each topic for queries like it.
    </p>

    <div class="flex flex-wrap items-center gap-2 mb-2">
      <input
        v-model="query"
        placeholder="a prompt — e.g. fix the recall ranking bug"
        class="flex-1 max-w-2xl min-w-[18rem] text-sm border border-slate-200 rounded-md px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-blue-500"
        @keyup.enter="preview"
      />
      <Button variant="primary" size="sm" :disabled="loading" @click="preview">Preview route</Button>
    </div>

    <template v-if="result">
      <!-- What the recall hook would actually inject (keyword route). The
           "why" line makes the basis legible: the route is decided by keyword
           overlap, NOT by the pos/neg exemplar scores in the table below. -->
      <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 mb-2 text-sm">
        <div class="flex items-center gap-2">
          <span class="text-[11px] uppercase tracking-wider text-slate-400">routes to</span>
          <template v-if="result.routed">
            <span class="font-medium text-slate-800">{{ result.routed.label }}</span>
            <span
              class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
              :class="rowState(result.routed).cls"
            >{{ rowState(result.routed).label }}</span>
            <span class="font-mono text-[11px] text-slate-400 ml-auto">+{{ sim(result.routed.pos_sim) }} / −{{ sim(result.routed.neg_sim) }}</span>
          </template>
          <span v-else class="text-slate-400 italic">no keyword match</span>
        </div>
        <p v-if="routeBasis" class="text-[11px] text-slate-400 mt-1 leading-relaxed">
          matched
          <template v-if="result.routed.keywords?.length">
            on
            <span v-for="kw in result.routed.keywords" :key="kw" class="font-mono text-slate-600 bg-slate-200/60 rounded px-1 mr-0.5">{{ kw }}</span>
          </template>
          <template v-else>the full query</template>
          <span class="text-slate-400">· {{ result.routed.strategy }}</span>
        </p>
        <p v-else-if="!result.routed" class="text-[11px] text-slate-400 mt-1 leading-relaxed">
          no topic label, alias, or ref path shares a meaningful keyword with this query — the recall hook would inject no topic.
        </p>
      </div>

      <!-- Topics whose exemplars lean on this query, ranked by the stronger signal. -->
      <div v-if="result.candidates.length" class="rounded-lg border border-slate-200 bg-white overflow-hidden mb-2">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
            <tr>
              <th class="text-left font-medium px-3 py-2">Topic</th>
              <th class="text-left font-medium px-3 py-2">State</th>
              <th class="text-right font-medium px-3 py-2">Pos</th>
              <th class="text-right font-medium px-3 py-2">Neg</th>
              <th class="text-right font-medium px-3 py-2">Label</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="c in result.candidates" :key="c.id">
              <tr class="border-t border-slate-100">
                <td class="px-3 py-2 text-slate-800 truncate max-w-[16rem]">
                  <Button variant="link" size="sm" class="text-left text-slate-800 hover:text-blue-600 hover:no-underline gap-1 focus-visible:ring-2 focus-visible:ring-blue-400" :title="`${expanded.has(c.id) ? 'Hide' : 'View'} cases`" @click="toggle(c.id)">
                    <Icon :name="expanded.has(c.id) ? 'chevron-down' : 'chevron-right'" :size="14" class="text-slate-400" />
                    <span class="font-medium">{{ c.label }}</span>
                  </Button>
                  <span class="font-mono text-[11px] text-slate-400 ml-1.5">{{ c.pos_count }}+ / {{ c.neg_count }}−</span>
                </td>
                <td class="px-3 py-2">
                  <span
                    class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                    :class="rowState(c).cls"
                  >{{ rowState(c).label }}</span>
                </td>
                <td class="px-3 py-2 text-right font-mono text-emerald-600">{{ sim(c.pos_sim) }}</td>
                <td class="px-3 py-2 text-right font-mono text-red-600">{{ sim(c.neg_sim) }}</td>
                <td class="px-3 py-2">
                  <div class="flex items-center justify-end gap-1.5">
                    <Button variant="secondary" size="sm" class="px-2 text-slate-500 hover:text-emerald-700 focus-visible:ring-2" :disabled="busy === `${c.id}:positive`" aria-label="Protect this topic for queries like this" title="Protect (positive)" @click="label(c.id, 'positive')"><Icon name="thumbs-up" :size="14" /></Button>
                    <Button variant="secondary" size="sm" class="px-2 text-slate-500 hover:text-red-700 focus-visible:ring-2" :disabled="busy === `${c.id}:negative`" aria-label="Suppress this topic for queries like this" title="Suppress (negative)" @click="label(c.id, 'negative')"><Icon name="thumbs-down" :size="14" /></Button>
                  </div>
                </td>
              </tr>
              <tr v-if="expanded.has(c.id)" :key="`${c.id}-cases`">
                <td colspan="5" class="p-0 border-t border-slate-100">
                  <ExemplarCaseList kind="topic" :target-id="c.id" @changed="preview" />
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
      <p v-else class="text-sm text-slate-400 px-1 mb-2">
        No topic carries an exemplar that resembles this query yet. Label one below to start a case.
      </p>

      <!-- Build a case against any topic, even one with no exemplars yet. -->
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <span class="text-[11px] text-slate-400">label this query for</span>
        <Select v-model="pickTopic" :options="topicOptions" class="text-[12px] min-w-[14rem] focus-visible:ring-2" />
        <Button variant="secondary" size="sm" class="gap-1.5 hover:text-emerald-700 focus-visible:ring-2" :disabled="busy === `${pickTopic}:positive`" @click="label(pickTopic, 'positive')"><Icon name="thumbs-up" :size="14" />protect</Button>
        <Button variant="secondary" size="sm" class="gap-1.5 hover:text-red-700 focus-visible:ring-2" :disabled="busy === `${pickTopic}:negative`" @click="label(pickTopic, 'negative')"><Icon name="thumbs-down" :size="14" />suppress</Button>
      </div>
    </template>
  </section>
</template>
