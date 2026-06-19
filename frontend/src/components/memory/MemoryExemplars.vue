<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Select from '../ui/Select.vue'
import Icon from '../ui/Icon.vue'
import ExemplarCaseList from './ExemplarCaseList.vue'

// Exemplar inspection + curation: which memories carry query exemplars (the
// contextual recall re-ranking loop) and how many of each sign. A negative is
// a prompt the memory was injected on then graded a hard ignore (demotes it
// for similar queries); a positive is one it engaged on, or a hand-curated
// case (boosts it). Both are query-local and leave importance untouched.
const negWeight = ref(0)
const posWeight = ref(0)
const summary = ref([])
const loading = ref(false)
const active = computed(() => negWeight.value > 0 || posWeight.value > 0)
const polarityOptions = [
  { value: 'positive', label: 'positive (boost)' },
  { value: 'negative', label: 'negative (demote)' },
]

// "Build a case" form — attach a positive/negative exemplar by hand.
const form = ref({ memoryId: '', query: '', polarity: 'positive' })
const saving = ref(false)
const formError = ref('')
const expanded = ref(new Set())  // memory ids whose case list is open

function toggle(memoryId) {
  const next = new Set(expanded.value)
  next.has(memoryId) ? next.delete(memoryId) : next.add(memoryId)
  expanded.value = next
}

async function reload() {
  loading.value = true
  try {
    const data = await api.get('/memory/exemplars')
    negWeight.value = data.neg_weight || 0
    posWeight.value = data.pos_weight || 0
    summary.value = data.summary || []
  } finally {
    loading.value = false
  }
}

async function addCase() {
  formError.value = ''
  if (!form.value.memoryId.trim() || !form.value.query.trim()) {
    formError.value = 'memory id and query are required'
    return
  }
  saving.value = true
  try {
    const res = await api.post('/memory/exemplars', {
      memory_id: form.value.memoryId.trim(),
      query: form.value.query.trim(),
      polarity: form.value.polarity,
    })
    if (res.ok === false) {
      formError.value = res.msg || 'failed to add case'
      return
    }
    form.value.query = ''
    await reload()
  } finally {
    saving.value = false
  }
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
  <section v-if="active || summary.length" class="mb-4 mt-4">
    <div class="flex items-center gap-2 mb-1">
      <h2 class="text-sm font-semibold text-slate-800">Recall exemplars</h2>
      <span class="text-[11px] text-slate-400 font-mono">{{ summary.length }} memories</span>
      <span
        class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
        :class="posWeight > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'"
      >{{ posWeight > 0 ? `boost ×${posWeight}` : 'boost off' }}</span>
      <span
        class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
        :class="negWeight > 0 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'"
      >{{ negWeight > 0 ? `demote ×${negWeight}` : 'demote off' }}</span>
    </div>
    <p class="text-xs text-slate-500 mb-3 leading-relaxed">
      Graded injects re-rank a memory for similar queries only — leaving its intrinsic importance untouched.
    </p>

    <!-- Build a case: hand-curate a positive/negative exemplar for a memory. -->
    <div class="flex flex-wrap items-center gap-2 mb-2 text-sm">
      <input
        v-model="form.memoryId"
        placeholder="memory id"
        class="rounded border border-slate-200 px-2 py-1 font-mono text-[12px] w-28 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
      />
      <input
        v-model="form.query"
        placeholder="example query this memory should (not) match"
        class="rounded border border-slate-200 px-2 py-1 flex-1 min-w-[16rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        @keyup.enter="addCase"
      />
      <Select v-model="form.polarity" :options="polarityOptions" class="text-[12px]" />
      <Button variant="primary" size="sm" class="focus-visible:ring-2" :disabled="saving" @click="addCase">Add case</Button>
      <span v-if="formError" class="text-[11px] text-red-600">{{ formError }}</span>
    </div>

    <p v-if="!summary.length" class="text-sm text-slate-400 px-1">
      No exemplars recorded yet. They accrue forward as graded sessions produce
      engaged / hard-ignore verdicts, or add one by hand above.
    </p>

    <div v-else class="rounded-lg border border-slate-200 bg-white overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-500">
          <tr>
            <th class="text-left font-medium px-3 py-2">Memory</th>
            <th class="text-left font-medium px-3 py-2">Kind</th>
            <th class="text-right font-medium px-3 py-2">Boost</th>
            <th class="text-right font-medium px-3 py-2">Demote</th>
            <th class="text-right font-medium px-3 py-2">Latest</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in summary" :key="s.memory_id">
            <tr class="border-t border-slate-100">
              <td class="px-3 py-2 text-slate-800 truncate max-w-[20rem]">
                <Button variant="link" size="sm" class="text-left text-slate-800 hover:text-blue-600 hover:no-underline gap-1 focus-visible:ring-2 focus-visible:ring-blue-400" :title="`${expanded.has(s.memory_id) ? 'Hide' : 'View'} cases`" @click="toggle(s.memory_id)">
                  <Icon :name="expanded.has(s.memory_id) ? 'chevron-down' : 'chevron-right'" :size="14" class="text-slate-400" />
                  <span class="font-medium">{{ s.title || '(untitled)' }}</span>
                </Button>
                <span class="font-mono text-[11px] text-slate-400 ml-1.5">{{ shortId(s.memory_id) }}</span>
              </td>
              <td class="px-3 py-2 text-slate-500">{{ s.kind }}</td>
              <td class="px-3 py-2 text-right font-mono text-emerald-600">{{ s.pos_count || '' }}</td>
              <td class="px-3 py-2 text-right font-mono text-amber-600">{{ s.neg_count || '' }}</td>
              <td class="px-3 py-2 text-right font-mono text-[11px] text-slate-400">{{ when(s.last_created) }}</td>
            </tr>
            <tr v-if="expanded.has(s.memory_id)" :key="`${s.memory_id}-cases`">
              <td colspan="5" class="p-0 border-t border-slate-100">
                <ExemplarCaseList kind="memory" :target-id="s.memory_id" @changed="reload" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </section>
</template>
