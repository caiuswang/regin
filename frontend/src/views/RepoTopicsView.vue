<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import AuditPanel from '../components/topics/AuditPanel.vue'
import HistoryPanel from '../components/topics/HistoryPanel.vue'
import { useBreakpoint } from '../composables/useBreakpoint'
import WikiWorkspace from '../components/topics/WikiWorkspace.vue'
import ProposalCreateCard from '../components/topics/ProposalCreateCard.vue'
import ProposalRunsList from '../components/topics/ProposalRunsList.vue'
import ProposalRunDetail from '../components/topics/ProposalRunDetail.vue'
import { useFlash } from '../composables/useFlash'

const route = useRoute()
const router = useRouter()
const { flash } = useFlash()

const repo = computed(() => route.params.name)
const workspace = computed(() => {
  if (route.query.tab === 'proposals') return 'proposals'
  if (route.query.tab === 'audit') return 'audit'
  // History tab hidden pending UX redesign — restore once snapshot
  // preview/restore is clearer than the current row+modal flow.
  // if (route.query.tab === 'history') return 'history'
  return 'wiki'
})

const loading = ref(true)
const error = ref('')
const proposalError = ref('')
const busyAction = ref('')

const summaryData = ref(null)
const wikiData = ref(null)
const proposalData = ref(null)
const wikiLoaded = ref(false)
const proposalLoaded = ref(false)

let proposalPollTimer = null

// List of approved topic ids — passed to ProposalRunDetail for DiffPanel's merge-target picker.
const approvedTopicIds = computed(() => (wikiData.value?.table || []).map((t) => t.id))

const activeProposalRuns = computed(() => (proposalData.value?.runs || []).filter((run) => ['queued', 'running', 'waiting_for_permission'].includes(run.state)))

const summaryStats = computed(() => {
  if (!summaryData.value) return []
  return [
    { label: 'Approved Topics', value: summaryData.value.approved_topic_count, tone: 'blue' },
    { label: 'Proposal Runs', value: summaryData.value.proposal_run_count, tone: 'purple' },
    { label: 'Broken Refs', value: summaryData.value.broken_ref_count, tone: summaryData.value.broken_ref_count ? 'red' : 'green' },
  ]
})

// On phones (and on any proposal-detail deep link) the full hero + stat tiles
// + create-proposal card push the actual content ~2 viewports down, so those
// blocks collapse to one-line summaries behind disclosure toggles.
const { isMdUp } = useBreakpoint()
const chromeExpanded = ref(false)
const proposalDetailOpen = computed(() => workspace.value === 'proposals' && Boolean(route.query.proposal))
const chromeCollapsible = computed(() => proposalDetailOpen.value || !isMdUp.value)
const compactChrome = computed(() => chromeCollapsible.value && !chromeExpanded.value)
const compactModes = computed(() => [
  { id: 'wiki', label: 'Approved', count: summaryData.value?.approved_topic_count || 0 },
  { id: 'proposals', label: 'Proposals', count: summaryData.value?.proposal_run_count || 0 },
  { id: 'audit', label: 'Audit', count: summaryData.value?.broken_ref_count || 0 },
])

function withQuery(next) {
  return { ...route.query, ...next }
}

function setWorkspace(nextWorkspace) {
  router.replace({ query: withQuery({ tab: nextWorkspace }) })
}

function startBusy(action) {
  busyAction.value = action
}

function stopBusy() {
  busyAction.value = ''
}

function isBusy(action = '') {
  if (!busyAction.value) return false
  return action ? busyAction.value === action : true
}

async function loadSummary() {
  summaryData.value = await api.get(`/repos/${repo.value}/topics/workspace/summary`)
}

async function loadWiki() {
  const params = new URLSearchParams()
  if (route.query.topic) params.set('topic_id', route.query.topic)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  wikiData.value = await api.get(`/repos/${repo.value}/topics/workspace/wiki${suffix}`)
  wikiLoaded.value = true
}

async function loadProposals() {
  proposalError.value = ''
  const params = new URLSearchParams()
  if (route.query.proposal) params.set('proposal_id', route.query.proposal)
  if (route.query.revision) params.set('revision_id', route.query.revision)
  if (route.query.draft) params.set('draft_topic_id', route.query.draft)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  proposalData.value = await api.get(`/repos/${repo.value}/topics/workspace/proposals${suffix}`)
  proposalLoaded.value = true
  refreshProposalPolling()
}

async function ensureWorkspaceData(mode, options = {}) {
  const force = Boolean(options.force)
  if (mode === 'proposals') {
    if (force || !proposalLoaded.value) await loadProposals()
    return
  }
  if (force || !wikiLoaded.value) await loadWiki()
}

async function load(mode = workspace.value) {
  loading.value = true
  error.value = ''
  try {
    await loadSummary()
    await ensureWorkspaceData(mode)
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function refreshSummaryAndWiki() {
  await Promise.all([loadSummary(), loadWiki()])
}

async function refreshSummaryAndProposals() {
  await Promise.all([loadSummary(), loadProposals()])
}

async function refreshCurrentWorkspace() {
  try {
    await loadSummary()
    await ensureWorkspaceData(workspace.value, { force: true })
  } catch (err) {
    error.value = err.message || String(err)
  }
}

async function refreshWikiSelection() {
  try {
    await loadWiki()
  } catch (err) {
    error.value = err.message || String(err)
  }
}

async function scanTopics() {
  startBusy('scan')
  try {
    const result = await api.post(`/repos/${repo.value}/topics/scan`, {})
    if (!result.ok) {
      error.value = result.msg || 'Scan failed'
      return
    }
    await refreshSummaryAndWiki()
  } finally {
    stopBusy()
  }
}

async function generateWiki() {
  startBusy('generate-wiki')
  try {
    const result = await api.post(`/repos/${repo.value}/topics/wiki`, {})
    if (!result.ok) {
      error.value = result.msg || 'Wiki generation failed'
      return
    }
    flash(`Generated ${result.count} wiki files.`)
    await refreshSummaryAndWiki()
  } finally {
    stopBusy()
  }
}

async function reindexWikis() {
  // POST /api/repos/<name>/wiki/reindex — refreshes the dense pattern
  // index for this repo's per-topic wiki pages. Auto-on-accept already
  // runs in the background; this button is a manual force-refresh +
  // visible feedback hook (e.g. after deleting a topic, or after a
  // fresh install with pre-existing accepted wikis).
  startBusy('reindex-wikis')
  try {
    const result = await api.post(`/repos/${repo.value}/topics/wiki/reindex`, {})
    if (!result.ok) {
      const base = result.msg || result.error || 'Wiki reindex failed'
      error.value = result.detail ? `${base}: ${result.detail}` : base
      return
    }
    const c = result.counts || {}
    flash(`Reindexed wikis — indexed=${c.indexed ?? 0} skipped=${c.skipped ?? 0} removed=${c.removed ?? 0} missing=${c.missing ?? 0}.`)
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    stopBusy()
  }
}

async function syncFromGit() {
  // The graph reseeds from disk on any read, but the dense wiki search
  // index does not — so this both imports git-shipped topics and rebuilds
  // that index, giving an explicit, feedback-bearing "sync" action.
  startBusy('sync-git')
  try {
    const result = await api.post(`/repos/${repo.value}/topics/import`, {})
    if (!result.ok) {
      error.value = result.msg || result.error || 'Sync failed'
      return
    }
    const n = result.topic_count ?? 0
    const idx = result.wiki_index || {}
    const indexNote = idx.error ? ' (wiki index skipped — embedding deps missing)' : ''
    const pb = result.proposal_backfill || {}
    const propNote = pb.imported ? ` + imported ${pb.imported} proposal run${pb.imported === 1 ? '' : 's'}` : ''
    flash(`Synced ${n} topic${n === 1 ? '' : 's'} from git${propNote} + rebuilt the wiki search index${indexNote}.`)
    await refreshSummaryAndWiki()
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    stopBusy()
  }
}

async function refreshSummaryWikiProposals() {
  await Promise.all([loadSummary(), loadWiki(), loadProposals()])
}

async function onProposalCreated(proposalId) {
  router.replace({ query: withQuery({ tab: 'proposals', proposal: proposalId, draft: undefined }) })
  await refreshSummaryAndProposals()
}

async function onDiffApplied() {
  await Promise.all([loadSummary(), loadWiki(), loadProposals()])
}

async function refreshActiveProposalRuns() {
  const activeRuns = activeProposalRuns.value
  if (!activeRuns.length || workspace.value !== 'proposals') {
    refreshProposalPolling()
    return
  }
  try {
    let changed = false
    for (const run of activeRuns) {
      const body = await api.get(`/repos/${repo.value}/topics/proposals/${run.id}/status`)
      if (body.status?.error) proposalError.value = body.status.error
      const next = body.status?.state
      // Refetch the workspace when the polled state differs from the one the
      // UI currently shows for this run — NOT from a persistent map. A
      // regenerate reuses the run id and replays queued→running→completed, so
      // a map entry left at 'completed' from the prior cycle masked the repeat
      // transition and stranded the surface on stale content until a manual
      // refresh. Comparing against the displayed state self-corrects: once the
      // refetch lands, displayed == polled and the 2.5s tick stops thrashing.
      if (next && next !== run.state) changed = true
    }
    if (changed) await refreshSummaryAndProposals()
  } catch (err) {
    proposalError.value = err.message || String(err)
  } finally {
    refreshProposalPolling()
  }
}

function refreshProposalPolling() {
  if (proposalPollTimer) {
    window.clearTimeout(proposalPollTimer)
    proposalPollTimer = null
  }
  if (workspace.value === 'proposals' && activeProposalRuns.value.length) {
    proposalPollTimer = window.setTimeout(refreshActiveProposalRuns, 2500)
  }
}

watch(
  () => workspace.value,
  async () => {
    await refreshCurrentWorkspace()
    refreshProposalPolling()
  },
)

watch(
  () => route.query.topic,
  async () => {
    if (workspace.value === 'wiki') await refreshWikiSelection()
  },
)

watch(
  () => [route.query.proposal, route.query.revision, route.query.draft],
  async () => {
    if (workspace.value === 'proposals') await refreshCurrentWorkspace()
  },
)

onMounted(async () => {
  await load()
})

onUnmounted(() => {
  if (proposalPollTimer) window.clearTimeout(proposalPollTimer)
})
</script>

<template>
  <div v-if="loading" class="empty-state">Loading topics workspace…</div>
  <div v-else class="topics-workspace">
    <section v-if="compactChrome" class="mb-5 space-y-2">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border bg-surface px-3 py-1.5">
        <span class="text-sm font-semibold text-fg">Topics Workspace</span>
        <router-link :to="`/repos/${repo}`" class="topics-repo-link focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
          {{ repo }}
        </router-link>
        <Button variant="ghost" size="sm" class="ml-auto min-h-9" @click="chromeExpanded = true">Show details</Button>
      </div>
      <div class="grid grid-cols-3 gap-1 rounded-xl border border-border bg-surface p-1" role="tablist" aria-label="Topics workspaces">
        <Button
          v-for="mode in compactModes"
          :key="mode.id"
          variant="ghost"
          size="sm"
          role="tab"
          class="min-h-10"
          :class="workspace === mode.id ? 'bg-surface-2 text-fg font-semibold' : ''"
          :aria-selected="workspace === mode.id ? 'true' : 'false'"
          @click="setWorkspace(mode.id)"
        >
          {{ mode.label }}
          <span class="text-xs text-fg-faint font-mono tabular-nums">{{ mode.count }}</span>
        </Button>
      </div>
    </section>
    <section v-else class="topics-shell">
      <div class="topics-shell-hero">
        <div class="topics-shell-copy">
          <div class="topics-shell-meta-row">
            <p class="topics-kicker">Knowledge Operations</p>
            <router-link :to="`/repos/${repo}`" class="topics-repo-link focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
              {{ repo }}
            </router-link>
            <Button v-if="chromeCollapsible" variant="ghost" size="sm" class="min-h-9" @click="chromeExpanded = false">Hide details</Button>
          </div>
          <h1 class="topics-title">Topics Workspace</h1>
          <p class="topics-subtitle">
            Review approved topics and manage proposal runs in one calm workflow for this repository’s topic graph.
          </p>
        </div>
        <div class="topics-shell-status">
          <div class="topics-status-card">
            <div class="topics-status-label">Proposal activity</div>
            <div class="topics-status-value">{{ summaryData?.active_proposal_count || 0 }}</div>
            <div class="topics-status-meta">runs currently in flight</div>
          </div>
          <div class="topics-status-card">
            <div class="topics-status-label">Reference health</div>
            <div class="topics-status-value">{{ summaryData?.broken_ref_count || 0 }}</div>
            <div class="topics-status-meta">broken refs to resolve</div>
          </div>
        </div>
      </div>

      <div class="topics-summary-grid">
        <article v-for="stat in summaryStats" :key="stat.label" class="topics-summary-card">
          <div class="topics-summary-card-top">
            <div class="topics-summary-label">{{ stat.label }}</div>
            <Badge :color="stat.tone" :label="stat.label" />
          </div>
          <div class="topics-summary-value">{{ stat.value }}</div>
        </article>
      </div>

      <div class="topics-shell-nav" role="tablist" aria-label="Topics workspaces">
        <Button
          variant="ghost"
          class="topics-mode-button h-auto justify-start whitespace-normal"
          :class="{ 'topics-mode-button-active': workspace === 'wiki' }"
          @click="setWorkspace('wiki')"
        >
          <div class="topics-mode-topline">
            <span class="topics-mode-label">Approved</span>
            <span class="topics-mode-count">{{ summaryData?.approved_topic_count || 0 }}</span>
          </div>
          <span class="topics-mode-meta">Canonical topics</span>
        </Button>
        <Button
          variant="ghost"
          class="topics-mode-button h-auto justify-start whitespace-normal"
          :class="{ 'topics-mode-button-active': workspace === 'proposals' }"
          @click="setWorkspace('proposals')"
        >
          <div class="topics-mode-topline">
            <span class="topics-mode-label">Proposals</span>
            <span class="topics-mode-count">{{ summaryData?.proposal_run_count || 0 }}</span>
          </div>
          <span class="topics-mode-meta">Draft runs</span>
        </Button>
        <Button
          variant="ghost"
          class="topics-mode-button h-auto justify-start whitespace-normal"
          :class="{ 'topics-mode-button-active': workspace === 'audit' }"
          @click="setWorkspace('audit')"
        >
          <div class="topics-mode-topline">
            <span class="topics-mode-label">Audit</span>
            <span class="topics-mode-count">{{ summaryData?.broken_ref_count || 0 }}</span>
          </div>
          <span class="topics-mode-meta">Graph health</span>
        </Button>
        <!--
          History tab hidden pending UX redesign — restore once snapshot
          preview/restore is clearer than the current row+modal flow.
          The panel template and HistoryPanel.vue are kept so the next
          iteration can re-enable without re-implementing.
        <button
          type="button"
          class="topics-mode-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :class="{ 'topics-mode-button-active': workspace === 'history' }"
          @click="setWorkspace('history')"
        >
          <div class="topics-mode-topline">
            <span class="topics-mode-label">History</span>
          </div>
          <span class="topics-mode-meta">Snapshots</span>
        </button>
        -->

      </div>
    </section>

    <div v-if="error" class="alert alert-info">{{ error }}</div>

    <template v-if="workspace === 'wiki'">
      <section class="topics-mode-header">
        <div class="topics-mode-copy">
          <h2 class="topics-section-title">Approved Topic Graph</h2>
          <p class="topics-mode-description">Maintain the canonical wiki-backed topic graph and inspect the references, edges, and wiki output behind each approved topic.</p>
        </div>
        <div class="topics-mode-actions btn-row">
          <Button variant="secondary" :disabled="isBusy()" @click="syncFromGit" title="Import teammate-committed topics from git into your local snapshot and rebuild the wiki search index. (The graph also auto-syncs whenever you open this page; this refreshes search too.)">{{ isBusy('sync-git') ? 'Syncing…' : 'Sync from git' }}</Button>
          <Button variant="secondary" :disabled="isBusy()" @click="generateWiki">Generate Wiki</Button>
          <Button variant="secondary" :disabled="isBusy()" @click="reindexWikis" title="Refresh the dense pattern index for this repo's per-topic wiki pages. Auto-runs after every accept; click to force-refresh.">{{ isBusy('reindex-wikis') ? 'Indexing…' : 'Re-index Wikis' }}</Button>
          <Button variant="primary" :disabled="isBusy()" @click="scanTopics">{{ isBusy('scan') ? 'Working...' : 'Scan Topics' }}</Button>
        </div>
      </section>

      <div v-if="wikiData?.validation && !wikiData.validation.ok" class="alert alert-info">
        {{ wikiData.validation.errors.join('; ') }}
      </div>

      <WikiWorkspace
        :repo="repo"
        :data="wikiData"
        @refresh-all="refreshSummaryWikiProposals"
        @error="(msg) => error = msg"
      />
    </template>

    <template v-else-if="workspace === 'proposals'">
      <section class="topics-mode-header">
        <div class="topics-mode-copy">
          <h2 class="topics-section-title">Proposal Operations</h2>
          <p class="topics-mode-description">Generate draft topic structures, monitor run status, and review suggested changes before they affect the approved graph.</p>
        </div>
      </section>

      <ProposalCreateCard
        :repo="repo"
        :data="proposalData"
        :busy="isBusy()"
        :compact="compactChrome"
        @created="onProposalCreated"
        @error="(msg) => proposalError = msg"
        @busy="(on) => on ? startBusy('create-proposal') : stopBusy()"
      />

      <div v-if="proposalError" class="alert alert-info">{{ proposalError }}</div>

      <ProposalRunsList
        v-if="!route.query.proposal"
        :repo="repo"
        :data="proposalData"
        :busy="isBusy()"
        @refresh="refreshSummaryAndProposals"
        @error="(msg) => proposalError = msg"
      />
      <ProposalRunDetail
        v-else
        :repo="repo"
        :data="proposalData"
        :wiki-loaded="wikiLoaded"
        :approved-topic-ids="approvedTopicIds"
        @refresh="refreshSummaryAndProposals"
        @refresh-all="onDiffApplied"
        @error="(msg) => proposalError = msg"
      />
    </template>

    <template v-else-if="workspace === 'audit'">
      <section class="topics-mode-header">
        <div class="topics-mode-copy">
          <h2 class="topics-section-title">Graph Audit</h2>
          <p class="topics-mode-description">Validation issues against the live approved graph, grouped by code.</p>
        </div>
      </section>
      <AuditPanel :repo-name="repo" />
    </template>

    <template v-else-if="workspace === 'history'">
      <section class="topics-mode-header">
        <div class="topics-mode-copy">
          <h2 class="topics-section-title">Snapshot History</h2>
          <p class="topics-mode-description">Every accept/merge/replace creates a snapshot. Restore brings an older state back as the new latest.</p>
        </div>
      </section>
      <HistoryPanel :repo-name="repo" @restored="loadSummary(); loadWiki()" />
    </template>
  </div>
</template>
