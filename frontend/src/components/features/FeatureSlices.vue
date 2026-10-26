<script setup>
// The spec's slice table, read by the server exactly as `regin task
// slice-check` / `transcribe` read it: the spec's problems (each with its line)
// or the refusal verbatim, and each row beside
// the task transcription made of it. The three verbs are the CLI's, over the
// same `lib/tasks` functions, so the ledger and a terminal cannot slice one
// spec two ways.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import FeatureBranch from './FeatureBranch.vue'
import TaskStatusIcon from '../tasks/TaskStatusIcon.vue'
import SliceArmSheet from './SliceArmSheet.vue'
import { SLICES_ANCHOR } from '../../utils/featureLinks'
import { ARM_SHEET_QUERY } from '../../utils/scopeHeadAction.js'

const props = defineProps({
  featureId: { type: Number, required: true },
  // Re-read whenever the spec changes: the table lives inside it.
  body: { type: String, default: '' },
  canWrite: { type: Boolean, default: false },
  // A tracked feature's spec is the tracker's (D4), so the tail is added there.
  tracked: { type: Boolean, default: false },
  projectId: { type: Number, default: null },
  // The feature's set integration branch, for the footer's editor.
  branch: { type: String, default: null },
})
const emit = defineEmits(['changed', 'branch-saved'])

const report = ref(null)
const loadError = ref('')
const busy = ref('')
const failed = ref('')
const refusedRows = ref([])
const arming = ref(false)

async function load() {
  loadError.value = ''
  try {
    report.value = await api.get(`/features/${props.featureId}/slices`)
  } catch (e) {
    loadError.value = e?.message || String(e)
  }
}
watch(() => [props.featureId, props.body], load, { immediate: true })

const rows = computed(() => report.value?.rows || [])
const ideas = computed(() => rows.value.filter(r => r.task_status === 'idea'))
const canTail = computed(() => props.canWrite && !props.tracked && report.value?.tail_missing)
const canTranscribe = computed(() => props.canWrite && report.value?.sliceable)
// The spec reader's problems — the list slice-check and transcribe refuse on.
const problems = computed(() => report.value?.problems || [])
const coverage = computed(() => report.value?.scenarios || { covered: 0, total: 0 })

// One writer at a time, and a refusal lands here beside the verbs (I6).
async function send(verb, path) {
  busy.value = verb
  failed.value = ''
  refusedRows.value = []
  const res = await api.post(`/features/${props.featureId}${path}`)
  busy.value = ''
  if (res?.ok === false) {
    failed.value = res.msg || `the ${verb} was refused`
    return null
  }
  return res
}

async function addTail() {
  const res = await send('tail', '/slices/tail')
  if (!res) return
  report.value = res.slices
  emit('changed', res.ledger)
}

async function transcribe() {
  const res = await send('transcribe', '/transcribe')
  if (!res) return
  refusedRows.value = res.refused || []
  await load()
  emit('changed')
}

// An arrival asking for the arm sheet (the scoped Tasks head's "Arm N
// slices") opens it once the idea rows are known, then drops the flag so a
// reload or Back does not reopen it.
const route = useRoute()
const router = useRouter()
watch(() => [route.query[ARM_SHEET_QUERY], report.value, loadError.value, ideas.value.length, props.canWrite],
  ([asked, rep, err, n, can]) => {
    if (!asked || !(rep || err)) return
    if (can && n) arming.value = true
    const query = { ...route.query }
    delete query[ARM_SHEET_QUERY]
    router.replace({ query, hash: route.hash })
  }, { immediate: true })

async function onArmed() {
  arming.value = false
  await load()
  emit('changed')
}
</script>

<template>
  <Card :id="SLICES_ANCHOR" data-testid="feature-slices">
    <div class="fs-head">
      <div>
        <h2 class="card-header fs-title">Slices</h2>
        <p class="fs-sub">the spec's slice table — transcribe it into tasks, then arm them</p>
        <p v-if="problems.length" class="fs-coverage" data-testid="slices-coverage">
          {{ coverage.covered }}/{{ coverage.total }} scenarios covered
        </p>
      </div>
      <div v-if="report" class="fs-verbs">
        <Button
          v-if="canTail" variant="ghost" size="sm" :disabled="!!busy"
          data-testid="slices-tail" @click="addTail"
        >{{ busy === 'tail' ? 'Adding…' : 'Add tail rows' }}</Button>
        <Button
          v-if="canTranscribe" variant="secondary" size="sm" :disabled="!!busy"
          data-testid="slices-transcribe" @click="transcribe"
        >{{ busy === 'transcribe' ? 'Transcribing…' : 'Transcribe' }}</Button>
        <Button
          v-if="canWrite && ideas.length" variant="primary" size="sm" :disabled="!!busy"
          data-testid="slices-arm-all" @click="arming = true"
        >Arm {{ ideas.length }}</Button>
      </div>
    </div>

    <p v-if="loadError" class="fs-fail" role="alert">{{ loadError }}</p>
    <p v-if="failed" class="fs-fail" role="alert" data-testid="slices-error">{{ failed }}</p>
    <ul v-if="refusedRows.length" class="fs-refused" data-testid="slices-refused">
      <li v-for="r in refusedRows" :key="r.n"><b>{{ r.n }}</b> — {{ r.reason }}</li>
    </ul>

    <ul v-if="problems.length" class="fs-refusal fs-problems" data-testid="slices-problems">
      <li v-for="(p, i) in problems" :key="i" :data-code="p.code">
        <span class="fs-line">{{ p.line != null ? `L${p.line}` : '—' }}</span>
        <code>{{ p.code }}</code> <b>{{ p.subject }}</b> — {{ p.message }}
      </li>
    </ul>
    <div v-else-if="report?.refusal" class="fs-refusal" data-testid="slices-refusal">
      <p>{{ report.refusal.message }}</p>
      <ul v-if="report.refusal.missing.length > 1">
        <li v-for="m in report.refusal.missing" :key="m">{{ m }}</li>
      </ul>
    </div>

    <p v-if="report && !rows.length" class="fs-note" data-testid="slices-empty">
      No slice table in the spec yet.
    </p>
    <div v-else-if="rows.length" class="ledger-grid fs-grid">
      <div class="fs-row ledger-colhead" data-pl-part="ledger-colhead" data-testid="slices-header">
        <span class="ledger-cell">#</span>
        <span class="ledger-cell">Slice</span>
        <span class="ledger-cell">Tests</span>
        <span class="ledger-cell fs-wide">After</span>
        <span class="ledger-cell fs-wide">Base</span>
        <span class="ledger-cell">Task</span>
      </div>
      <ul class="ledger-list fs-list" data-testid="slices-rows">
        <li
          v-for="r in rows" :key="r.n" class="ledger-row fs-row" data-pl-part="ledger-row"
          :data-testid="`slice-row-${r.n}`"
        >
          <span class="ledger-cell fs-n" data-cell="n">{{ r.n }}</span>
          <span class="ledger-cell fs-name" data-cell="title" :title="r.title">
            <span class="ledger-ellipsis" :data-testid="`slice-title-${r.n}`">{{ r.title }}</span>
          </span>
          <span class="ledger-cell fs-meta" data-cell="tests" :title="r.tests?.join('\n') || undefined"
                :data-testid="`slice-tests-${r.n}`">{{ r.tests?.length ? `${r.tests.length} tests` : '' }}</span>
          <span class="ledger-cell fs-meta fs-wide" data-cell="after"
                :title="r.depends_on.join(', ') || undefined">
            <span class="ledger-ellipsis">{{ r.depends_on.join(', ') }}</span>
          </span>
          <span class="ledger-cell fs-meta fs-wide" data-cell="base" :title="r.base || undefined">
            <span class="ledger-ellipsis">{{ r.base }}</span>
          </span>
          <span class="ledger-cell" data-cell="task">
            <router-link
              v-if="r.task_id" class="fs-task" :to="`/tasks/${r.task_id}`"
              :data-testid="`slice-task-${r.n}`"
            >
              <TaskStatusIcon :status="r.task_status" :size="12" />
              #{{ r.task_id }} <span class="ledger-ellipsis">{{ r.task_status }}</span>
            </router-link>
            <span v-else class="fs-meta">not transcribed</span>
            <router-link
              v-for="d in r.fixes_defects || []" :key="d"
              class="ledger-tag" :class="{ 'ledger-tag-amber': r.fixes_open_defects?.includes(d) }"
              :to="`/defects/${d}`" data-pl-part="tag"
              :data-tone="r.fixes_open_defects?.includes(d) ? 'warn' : undefined"
              :data-testid="`slice-fixes-${r.n}-${d}`"
            >fixes #{{ d }}</router-link>
          </span>
        </li>
      </ul>
    </div>

    <FeatureBranch
      :feature-id="featureId" :branch="branch" :can-write="canWrite"
      @saved="emit('branch-saved', $event)"
    />

    <SliceArmSheet
      v-if="arming"
      :feature-id="featureId"
      :rows="ideas"
      :project-id="projectId"
      @close="arming = false"
      @armed="onArmed"
    />
  </Card>
</template>

<style scoped>
.fs-head {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem; margin-bottom: 0.6rem; flex-wrap: wrap;
}
.fs-title { margin-bottom: 0; }
.fs-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.fs-verbs { display: flex; align-items: center; gap: 0.35rem; flex: none; }
.fs-note { font-size: 11.5px; color: var(--color-fg-muted); }
.fs-fail, .fs-refusal, .fs-refused {
    margin: 0.4rem 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.35rem 0.5rem;
}
.fs-refusal ul, .fs-refused { list-style: disc; padding-left: 1.4rem; }
.fs-coverage { font-size: 11.5px; color: var(--color-amber-800); margin-top: 0.15rem; }
.fs-problems { list-style: none; display: flex; flex-direction: column; gap: 0.15rem; }
.fs-line { font-family: var(--font-mono, monospace); color: var(--color-fg-subtle); margin-right: 0.35rem; }
.fs-row {
    display: grid; align-items: center; column-gap: 0.5rem;
    grid-template-columns: 3rem minmax(0, 1fr) 5rem 8rem 8rem 12rem;
}
.fs-list { margin-top: 0; }
.fs-list .fs-row { grid-auto-rows: minmax(1.5rem, auto); }
@container (max-width: 719.98px) {
    .fs-row { grid-template-columns: 3rem minmax(0, 1fr) 5rem 12rem; }
    .fs-wide { display: none; }
}
.fs-n { font-family: var(--font-mono, monospace); font-size: 11.5px; color: var(--color-fg-faint); }
.fs-name { font-size: 0.8125rem; color: var(--color-fg); }
.fs-meta { font-size: 11px; color: var(--color-fg-subtle); }
.fs-task {
    display: inline-flex; align-items: center; gap: 0.3rem; flex: 0 1 auto; min-width: 0;
    font-size: 11.5px; color: var(--color-info); text-decoration: none;
}
.fs-task:hover { text-decoration: underline; }
.fs-task:focus-visible { outline: 2px solid var(--color-blue-500); }
</style>
