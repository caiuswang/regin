<script setup>
/**
 * RevisionCompareView — compare two revisions of one proposal across all the
 * info a reviewer cares about: proposal context (title/intent/state), per-side
 * revision metadata, then per-topic wiki content, reference files, and graph
 * metadata. Reuses `workspace/proposals?proposal_id=&revision_id=` (fetched
 * once per side) — no new backend surface.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import Select from '../components/ui/Select.vue'
import WikiContentDiff from '../components/topics/WikiContentDiff.vue'
import TopicComparisonCard from '../components/topics/TopicComparisonCard.vue'
import { fmtAgo } from '../utils/traceFormatters'

const route = useRoute()
const router = useRouter()

const repo = computed(() => route.params.name)
const proposalId = computed(() => route.query.proposal || '')

const run = ref(null)
const revisions = ref([])
const leftId = ref(null)
const rightId = ref(null)
const leftProposal = ref(null)
const rightProposal = ref(null)
const loading = ref(true)
const error = ref('')

const proposalTitle = computed(() => run.value?.title || run.value?.topic_request || proposalId.value || 'Proposal')
const revisionOptions = computed(() =>
  revisions.value.map((r) => ({
    value: r.id,
    label: `r${r.revision_number} · ${r.kind}${r.is_latest ? ' (latest)' : ''}`,
  })),
)
const hasEnough = computed(() => revisions.value.length >= 2)
const leftRev = computed(() => revisions.value.find((r) => r.id === leftId.value) || null)
const rightRev = computed(() => revisions.value.find((r) => r.id === rightId.value) || null)

const topicPairs = computed(() => {
  const before = new Map((leftProposal.value?.topics || []).map((t) => [t.id, t]))
  const after = new Map((rightProposal.value?.topics || []).map((t) => [t.id, t]))
  const ids = [...new Set([...before.keys(), ...after.keys()])]
  return ids.map((id) => ({ id, before: before.get(id) || null, after: after.get(id) || null }))
})

function stateColor(state) {
  if (state === 'completed') return 'green'
  if (state === 'failed' || state === 'timed_out') return 'red'
  if (state === 'running' || state === 'queued') return 'blue'
  return 'gray'
}

function revMeta(rev) {
  if (!rev) return ''
  const age = fmtAgo(rev.created_at)
  return `r${rev.revision_number} · ${rev.kind}${age ? ' · ' + age : ''}`
}

// Only honor a query id that names a real revision — a stale or hand-edited id
// would otherwise fall back server-side to the latest revision on both sides,
// silently showing an empty (left===right) diff with no error.
function validId(raw, fallback) {
  const id = Number(raw)
  return revisions.value.some((r) => r.id === id) ? id : fallback
}

function workspaceUrl(revisionId) {
  const params = new URLSearchParams({ proposal_id: proposalId.value })
  if (revisionId) params.set('revision_id', String(revisionId))
  return `/repos/${repo.value}/topics/workspace/proposals?${params.toString()}`
}

async function loadRevisions() {
  const data = await api.get(workspaceUrl(null))
  run.value = data.selected_run || null
  revisions.value = data.revisions || []
  const latest = revisions.value[0]?.id || null
  const previous = revisions.value[1]?.id || latest
  if (!rightId.value) rightId.value = validId(route.query.right, latest)
  if (!leftId.value) leftId.value = validId(route.query.left, previous)
}

async function loadSide(revisionId) {
  if (!revisionId) return null
  const data = await api.get(workspaceUrl(revisionId))
  return data.proposal || null
}

async function reloadBodies() {
  if (!hasEnough.value) return
  ;[leftProposal.value, rightProposal.value] = await Promise.all([
    loadSide(leftId.value),
    loadSide(rightId.value),
  ])
}

async function loadAll() {
  if (!proposalId.value) { loading.value = false; return }
  loading.value = true
  error.value = ''
  try {
    await loadRevisions()
    await reloadBodies()
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

function syncQuery() {
  router.replace({
    query: { ...route.query, left: leftId.value || undefined, right: rightId.value || undefined },
  })
}

watch([leftId, rightId], () => {
  syncQuery()
  reloadBodies().catch((err) => { error.value = err?.message || String(err) })
})

function backToProposal() {
  router.push({
    name: 'repo-topics',
    params: { name: repo.value },
    query: { tab: 'proposals', proposal: proposalId.value },
  })
}

onMounted(loadAll)
</script>

<template>
  <div class="space-y-5">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Compare revisions</div>
        <h1 class="page-title">{{ proposalTitle }}</h1>
        <p class="page-subtitle">
          <code class="revcompare__id">{{ proposalId || '—' }}</code>
          <template v-if="run">
            <Badge :color="stateColor(run.state)" :label="run.state || 'completed'" />
            <span v-if="run.provider" class="revcompare__dim">{{ run.provider }}<span v-if="run.agent"> · {{ run.agent }}</span></span>
          </template>
        </p>
      </div>
      <div class="page-actions">
        <Button variant="secondary" @click="backToProposal">← Back to proposal</Button>
      </div>
    </header>

    <p v-if="error" class="revcompare__notice revcompare__notice--error" data-testid="revcompare-error">{{ error }}</p>

    <p v-else-if="!proposalId" class="revcompare__notice" data-testid="revcompare-no-proposal">
      No proposal selected — open a proposal and choose “Compare revisions”.
    </p>

    <p v-else-if="loading" class="revcompare__notice">Loading revisions…</p>

    <p v-else-if="!hasEnough" class="revcompare__notice" data-testid="revcompare-too-few">
      This proposal has {{ revisions.length }} revision{{ revisions.length === 1 ? '' : 's' }} — at least two are needed to compare.
    </p>

    <template v-else>
      <div class="card revcompare__pickers">
        <div class="revcompare__picker">
          <label class="revcompare__picker-label">Base</label>
          <!-- native <select> emits a string; coerce so int revision-id
               comparisons (labels, defaulting) stay type-correct -->
          <Select
            :model-value="leftId"
            :options="revisionOptions"
            aria-label="Base revision"
            data-testid="revcompare-left"
            @update:model-value="leftId = Number($event)"
          />
          <span class="revcompare__picker-meta">{{ revMeta(leftRev) }}</span>
        </div>
        <span class="revcompare__arrow" aria-hidden="true">→</span>
        <div class="revcompare__picker">
          <label class="revcompare__picker-label">Compare</label>
          <Select
            :model-value="rightId"
            :options="revisionOptions"
            aria-label="Compare revision"
            data-testid="revcompare-right"
            @update:model-value="rightId = Number($event)"
          />
          <span class="revcompare__picker-meta">{{ revMeta(rightRev) }}</span>
        </div>
      </div>

      <div v-if="topicPairs.length" class="revcompare__cards">
        <TopicComparisonCard
          v-for="pair in topicPairs"
          :key="pair.id"
          :topic-id="pair.id"
          :before="pair.before"
          :after="pair.after"
          :before-label="`r${leftRev?.revision_number ?? '?'}`"
          :after-label="`r${rightRev?.revision_number ?? '?'}`"
        />
      </div>

      <!-- Legacy proposals with no per-topic rows: fall back to the combined wiki. -->
      <div v-else class="card" data-testid="revcompare-combined">
        <WikiContentDiff
          :before="leftProposal?.wiki || ''"
          :after="rightProposal?.wiki || ''"
          :before-label="`r${leftRev?.revision_number ?? '?'}`"
          :after-label="`r${rightRev?.revision_number ?? '?'}`"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.revcompare__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.75rem;
  padding: 0.0625rem 0.375rem;
  background: var(--color-surface-2);
  border-radius: 0.375rem;
  color: var(--color-slate-600);
}
.page-subtitle { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.revcompare__dim { color: var(--color-slate-400); }

.revcompare__notice {
  padding: 1rem;
  font-size: 0.85rem;
  color: var(--color-slate-500);
  background: var(--color-surface-2);
  border: 1px solid var(--color-slate-200);
  border-radius: 0.75rem;
}
.revcompare__notice--error {
  color: var(--color-red-700);
  background: var(--color-red-50);
  border-color: var(--color-red-200);
}

.revcompare__pickers {
  display: flex;
  align-items: flex-end;
  gap: 1rem;
  flex-wrap: wrap;
}
.revcompare__picker {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 14rem;
  flex: 1;
}
.revcompare__picker-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-slate-500);
}
.revcompare__picker-meta { font-size: 0.6875rem; color: var(--color-slate-400); }
.revcompare__arrow { padding-bottom: 0.5rem; color: var(--color-slate-400); }

.revcompare__cards { display: flex; flex-direction: column; gap: 1rem; }
</style>
