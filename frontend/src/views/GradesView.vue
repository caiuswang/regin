<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api'
import GradeStatBand from '../components/grades/GradeStatBand.vue'
import GradeRunPanel from '../components/grades/GradeRunPanel.vue'
import GradeRow from '../components/grades/GradeRow.vue'
import GradeAspectsConfig from '../components/GradeAspectsConfig.vue'
import Select from '../components/ui/Select.vue'
import { worstTone } from '../constants/gradeVerdicts'
import { useFlash } from '../composables/useFlash'

const { flash } = useFlash()
const grades = ref(null)
const summary = ref(null)
const points = ref([])
const expanded = ref(null)
const grading = ref(false)

// Judge config flows up from the embedded GradeAspectsConfig's single fetch, so
// the run panel never fetches twice.
const providers = ref([])
const defaultProvider = ref('')
const gradeableAspects = ref([])

const verdictFilter = ref('all')
const sortBy = ref('recent')

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'pass', label: 'Satisfied' },
  { key: 'warn', label: 'Needs work' },
  { key: 'fail', label: 'Failed' },
]
const SORTS = [
  { value: 'recent', label: 'Newest first' },
  { value: 'worst', label: 'Problems first' },
  { value: 'cost', label: 'Costliest first' },
]

function onConfigLoaded({ providers: list, externalAgent, aspects }) {
  providers.value = list || []
  if (!defaultProvider.value) defaultProvider.value = externalAgent || ''
  gradeableAspects.value = (aspects || [])
    .filter(a => !a.builtin)
    .map(a => ({ key: a.key, label: a.label }))
}

async function load() {
  try {
    const [list, pareto] = await Promise.all([
      api.get('/grades?limit=100'),
      api.get('/grades/pareto'),
    ])
    grades.value = list.grades
    summary.value = pareto.summary
    points.value = pareto.points || []
  } catch {
    grades.value = []
    flash('failed to load grades', 'error')
  }
}

// One row per session, axes side by side, with the pareto point (off-frontier
// flags, cost) merged in by trace id.
const rows = computed(() => {
  const byPoint = new Map((points.value || []).map(p => [p.trace_id, p]))
  const byTrace = new Map()
  for (const g of grades.value || []) {
    if (!byTrace.has(g.trace_id)) {
      byTrace.set(g.trace_id, {
        trace_id: g.trace_id, session: g.session, axes: {},
        point: byPoint.get(g.trace_id) || null,
      })
    }
    byTrace.get(g.trace_id).axes[g.axis] = g
  }
  return [...byTrace.values()]
})

function rowTone(row) {
  return worstTone(Object.values(row.axes).map(g => g.verdict))
}

const counts = computed(() => {
  const c = { all: rows.value.length, pass: 0, warn: 0, fail: 0 }
  for (const r of rows.value) {
    const t = rowTone(r)
    if (t in c) c[t] += 1
  }
  return c
})

const visibleRows = computed(() => {
  let out = rows.value
  if (verdictFilter.value !== 'all') {
    out = out.filter(r => rowTone(r) === verdictFilter.value)
  }
  const TONE_RANK = { fail: 0, warn: 1, pass: 2, unknown: 3 }
  if (sortBy.value === 'cost') {
    out = [...out].sort((a, b) =>
      (b.session?.cost_usd || 0) - (a.session?.cost_usd || 0))
  } else if (sortBy.value === 'worst') {
    out = [...out].sort((a, b) => TONE_RANK[rowTone(a)] - TONE_RANK[rowTone(b)])
  }
  return out
})

const frontier = computed(() => ({
  cheaplyWrong: (points.value || []).filter(p => p.cheaply_wrong).length,
  expensivelyRight: (points.value || []).filter(p => p.expensively_right).length,
}))

function toggle(traceId) {
  expanded.value = expanded.value === traceId ? null : traceId
}

async function gradeNow({ traceId, tier, body, onDone }) {
  grading.value = true
  try {
    const res = await api.post(`/sessions/${traceId}/grade`, body)
    if (res && res.ok === false) {
      flash(res.msg || 'grading failed — is the trace id right?', 'error')
      return
    }
    const verdict = res?.grades?.correctness?.verdict
    flash(`graded ${traceId.slice(0, 8)} (${tier})`
      + (verdict ? ` — correctness: ${verdict}` : ''))
    onDone?.()
    await load()
  } catch {
    flash('grading failed — is the trace id right?', 'error')
  } finally {
    grading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-if="grades == null" class="empty-state">Loading grades…</div>
  <div v-else class="space-y-5">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Observability</div>
        <h1 class="page-title">Grades</h1>
        <p class="page-subtitle">
          Post-hoc rubric grades: correctness (claims vs trace evidence)
          and process (trajectory efficiency), never fused into one number.
        </p>
      </div>
    </header>

    <GradeStatBand :summary="summary" :rows="rows" :frontier="frontier" />

    <GradeRunPanel
      :providers="providers"
      :aspects="gradeableAspects"
      :default-provider="defaultProvider"
      :grading="grading"
      @grade="gradeNow"
    />

    <GradeAspectsConfig @config-loaded="onConfigLoaded" />

    <div v-if="!rows.length" class="empty-state">
      No grades stored yet — run <code>regin grade run &lt;trace-id&gt;</code>
      or grade a session above.
    </div>

    <template v-else>
      <!-- Filter + sort bar -->
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-1.5">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            type="button"
            :class="[
              'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500 max-sm:min-h-9',
              verdictFilter === f.key
                ? 'border-blue-200 bg-blue-50 text-blue-700'
                : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700',
            ]"
            @click="verdictFilter = f.key"
          >
            {{ f.label }}
            <span class="tabular-nums opacity-60">{{ counts[f.key] }}</span>
          </button>
        </div>
        <label class="flex items-center gap-2 text-xs text-slate-500">
          Sort
          <Select v-model="sortBy" :options="SORTS" class="min-w-40" aria-label="Sort grades" />
        </label>
      </div>

      <div v-if="!visibleRows.length" class="empty-state">
        No sessions match this filter.
      </div>
      <div v-else class="space-y-2.5">
        <GradeRow
          v-for="row in visibleRows"
          :key="row.trace_id"
          :row="row"
          :expanded="expanded === row.trace_id"
          @toggle="toggle(row.trace_id)"
        />
      </div>
    </template>
  </div>
</template>
