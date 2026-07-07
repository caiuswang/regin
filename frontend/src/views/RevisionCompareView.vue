<script setup>
/**
 * RevisionCompareView — compare the wiki content of two revisions of one
 * proposal run, side by side as a unified diff.
 *
 * Reuses the existing `workspace/proposals?proposal_id=&revision_id=`
 * endpoint (fetched once per side) — no new backend surface. The revision
 * bodies come from the server; WikiContentDiff renders the line diff.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Button from '../components/ui/Button.vue'
import Select from '../components/ui/Select.vue'
import WikiContentDiff from '../components/topics/WikiContentDiff.vue'

const route = useRoute()
const router = useRouter()

const repo = computed(() => route.params.name)
const proposalId = computed(() => route.query.proposal || '')

const revisions = ref([])
const leftId = ref(null)
const rightId = ref(null)
const leftWiki = ref('')
const rightWiki = ref('')
const loading = ref(true)
const error = ref('')

const revisionOptions = computed(() =>
  revisions.value.map((r) => ({
    value: r.id,
    label: `r${r.revision_number} · ${r.kind}${r.is_latest ? ' (latest)' : ''}`,
  })),
)
const hasEnough = computed(() => revisions.value.length >= 2)
const leftLabel = computed(() => revisionLabel(leftId.value))
const rightLabel = computed(() => revisionLabel(rightId.value))

function revisionLabel(id) {
  const rev = revisions.value.find((r) => r.id === id)
  return rev ? `r${rev.revision_number}` : '—'
}

function workspaceUrl(revisionId) {
  const params = new URLSearchParams({ proposal_id: proposalId.value })
  if (revisionId) params.set('revision_id', String(revisionId))
  return `/repos/${repo.value}/topics/workspace/proposals?${params.toString()}`
}

// Only honor a query id that names a real revision — a stale or hand-edited
// id would otherwise fall back server-side to the latest revision on both
// sides, silently showing an empty (left===right) diff with no error.
function validId(raw, fallback) {
  const id = Number(raw)
  return revisions.value.some((r) => r.id === id) ? id : fallback
}

async function loadRevisions() {
  const data = await api.get(workspaceUrl(null))
  revisions.value = data.revisions || []
  // Revisions come newest-first; default to comparing the previous
  // revision (left) against the latest (right).
  const latest = revisions.value[0]?.id || null
  const previous = revisions.value[1]?.id || latest
  if (!rightId.value) rightId.value = validId(route.query.right, latest)
  if (!leftId.value) leftId.value = validId(route.query.left, previous)
}

async function loadSide(revisionId) {
  if (!revisionId) return ''
  const data = await api.get(workspaceUrl(revisionId))
  return data.proposal?.wiki || data.wiki_preview || ''
}

async function reloadBodies() {
  if (!hasEnough.value) return
  ;[leftWiki.value, rightWiki.value] = await Promise.all([
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
  <div class="revcompare">
    <header class="revcompare__head">
      <div>
        <div class="revcompare__eyebrow">Compare revisions</div>
        <h1 class="revcompare__title">
          <code>{{ proposalId || '—' }}</code>
        </h1>
      </div>
      <Button variant="secondary" size="sm" @click="backToProposal">← Back to proposal</Button>
    </header>

    <p v-if="error" class="revcompare__error" data-testid="revcompare-error">{{ error }}</p>

    <p v-else-if="!proposalId" class="revcompare__empty" data-testid="revcompare-no-proposal">
      No proposal selected — open a proposal and choose “Compare revisions”.
    </p>

    <p v-else-if="loading" class="revcompare__empty">Loading revisions…</p>

    <p v-else-if="!hasEnough" class="revcompare__empty" data-testid="revcompare-too-few">
      This proposal has {{ revisions.length }} revision{{ revisions.length === 1 ? '' : 's' }} — at least two are needed to compare.
    </p>

    <template v-else>
      <div class="revcompare__pickers">
        <label class="revcompare__picker">
          <span>Base</span>
          <!-- native <select> emits a string; coerce so the int revision-id
               comparisons (labels, defaulting) stay type-correct -->
          <Select
            :model-value="leftId"
            @update:model-value="leftId = Number($event)"
            :options="revisionOptions"
            aria-label="Base revision"
            data-testid="revcompare-left"
          />
        </label>
        <span class="revcompare__arrow" aria-hidden="true">→</span>
        <label class="revcompare__picker">
          <span>Compare</span>
          <Select
            :model-value="rightId"
            @update:model-value="rightId = Number($event)"
            :options="revisionOptions"
            aria-label="Compare revision"
            data-testid="revcompare-right"
          />
        </label>
      </div>

      <WikiContentDiff
        :before="leftWiki"
        :after="rightWiki"
        :before-label="leftLabel"
        :after-label="rightLabel"
      />
    </template>
  </div>
</template>

<style scoped>
.revcompare {
  padding: 1.5rem;
  max-width: 64rem;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.revcompare__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.revcompare__eyebrow {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-400);
}
.revcompare__title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--color-slate-900);
}
.revcompare__title code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9rem;
}
.revcompare__pickers {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.revcompare__picker {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.75rem;
  color: var(--color-slate-500);
}
.revcompare__arrow {
  padding-bottom: 0.4rem;
  color: var(--color-slate-400);
}
.revcompare__empty {
  padding: 1rem;
  font-size: 0.85rem;
  color: var(--color-slate-500);
  background: var(--color-slate-50);
  border: 1px solid var(--color-slate-200);
  border-radius: 0.625rem;
}
.revcompare__error {
  padding: 0.75rem 1rem;
  font-size: 0.8rem;
  color: var(--color-red-700);
  background: var(--color-red-50);
  border: 1px solid var(--color-red-200);
  border-radius: 0.625rem;
}
</style>
