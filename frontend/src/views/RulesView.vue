<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'

const route = useRoute()
const router = useRouter()

const data = ref(null)
const scriptsData = ref(null)
const repos = ref([])

const activeTab = computed(() => route.query.tab === 'scripts' ? 'scripts' : 'gritql')
const repoFilter = computed(() => route.query.repo || '')

const engineSummary = computed(() => {
  const engines = data.value?.engines || []
  if (!engines.length) return 'No rule engines configured.'
  if (engines.length === 1) {
    const engine = engines[0]
    return `${engine.rule_count} rules from the configured ${engine.kind} engine.`
  }
  return `${engines.length} configured rule engines, ${data.value?.total || 0} total rules.`
})

function setTab(tab) {
  const q = { ...route.query, tab }
  if (tab === 'gritql') delete q.tab
  router.push({ query: q })
}

async function loadGritql() {
  const by = route.query.by || 'guide'
  const repo = route.query.repo
  const repoArg = repo ? `&repo=${encodeURIComponent(repo)}` : ''
  data.value = await api.get(`/rules?by=${by}${repoArg}`)
}

async function loadScripts() {
  scriptsData.value = await api.get('/pattern-scripts')
}

async function loadRepos() {
  try {
    const resp = await api.get('/repos')
    repos.value = resp.repos || []
  } catch {
    repos.value = []
  }
}

function loadActive() {
  if (activeTab.value === 'gritql' && !data.value) loadGritql()
  if (activeTab.value === 'scripts' && !scriptsData.value) loadScripts()
}

onMounted(() => { loadActive(); loadRepos() })
watch(() => route.query.by, () => { data.value = null; loadGritql() })
watch(() => route.query.repo, () => { data.value = null; loadGritql() })
watch(activeTab, loadActive)

function setGroup(by) {
  router.push({ query: { ...route.query, by } })
}

function setRepo(repoName) {
  const q = { ...route.query }
  if (repoName) q.repo = repoName
  else delete q.repo
  router.push({ query: q })
}

function repoNameForId(projectId) {
  const match = repos.value.find(r => r.id === projectId)
  return match ? match.name : `repo#${projectId}`
}

function ruleScopeLabel(scope) {
  if (!scope) return null
  if (scope.global) return { color: 'gray', label: 'Global' }
  if (scope.project_ids && scope.project_ids.length) {
    const names = scope.project_ids.map(repoNameForId).join(', ')
    return { color: 'blue', label: names }
  }
  return { color: 'yellow', label: 'Undeployed' }
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Engineering</div>
        <h1 class="page-title">Rules</h1>
        <p class="page-subtitle">Rule definitions per configured engine, plus per-pattern runner scripts.</p>
      </div>
    </header>

    <div class="segmented mb-5">
      <button type="button"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': activeTab === 'gritql' }"
        @click="setTab('gritql')">
        Rules <span class="seg-count">{{ data ? data.total : '…' }}</span>
      </button>
      <button type="button"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': activeTab === 'scripts' }"
        @click="setTab('scripts')">
        Scripts <span class="seg-count">{{ scriptsData ? scriptsData.total_scripts : '…' }}</span>
      </button>
    </div>

    <!-- GritQL tab -->
    <div v-if="activeTab === 'gritql'">
      <div v-if="!data" class="empty-state">Loading rules…</div>
      <div v-else>
        <p class="page-subtitle mb-4">
          <strong>{{ data.total }}</strong> rules loaded. {{ engineSummary }}
          Per-file checks run via the PostToolUse hook when a rule engine decides a changed file is applicable.
        </p>

        <div v-if="data.engines?.length" class="mb-4 flex flex-wrap gap-2">
          <Badge
            v-for="engine in data.engines"
            :key="engine.id"
            color="blue"
            :label="`${engine.id} · ${engine.kind} · ${engine.rule_count}`"
          />
        </div>

        <div class="filter-row mb-4">
          <span class="filter-row-label">Group by:</span>
          <button type="button"
            class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
            :class="{ active: data.group_by === 'guide' }"
            @click="setGroup('guide')">Pattern</button>
          <button type="button"
            class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
            :class="{ active: data.group_by === 'layer' }"
            @click="setGroup('layer')">Layer</button>
          <span class="filter-row-label ml-4">Repo:</span>
          <select
            :value="repoFilter"
            @change="setRepo($event.target.value)"
            class="input filter-select focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter rules by repo"
          >
            <option value="">All repos</option>
            <option v-for="r in repos" :key="r.id" :value="r.name">{{ r.name }}</option>
          </select>
        </div>

        <div v-for="[groupKey, groupRules] in data.grouped" :key="groupKey" class="mb-5">
          <Card :no-padding="true">
            <div class="card-group-header flex items-center justify-between">
              <h2 class="card-group-title">
                <router-link v-if="data.group_by === 'guide'" :to="`/patterns/${groupKey}`"
                  class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">{{ groupKey }}</router-link>
                <code v-else>{{ groupKey }}</code>
              </h2>
              <span class="text-xs text-slate-400 font-mono">{{ groupRules.length }} rule{{ groupRules.length !== 1 ? 's' : '' }}</span>
            </div>
            <table class="tbl">
              <thead>
                <tr>
                  <th>Rule</th><th>Engine</th><th>Triggers</th><th>Severity</th>
                  <th v-if="data.group_by === 'layer'">Guide</th>
                  <th v-else>Layer</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in groupRules" :key="r.id" :class="{ 'opacity-40': r.disabled }">
                  <td>
                    <router-link :to="`/rules/${r.id}`"
                      class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                      <code class="break-all">{{ r.id }}</code>
                    </router-link>
                    <Badge v-if="r.disabled" color="gray" label="disabled" class="ml-1" />
                  </td>
                  <td>
                    <div class="flex flex-wrap gap-1">
                      <Badge color="gray" :label="r.engine_kind || r.engine" />
                      <Badge
                        v-if="ruleScopeLabel(r.scope)"
                        :color="ruleScopeLabel(r.scope).color"
                        :label="ruleScopeLabel(r.scope).label"
                      />
                    </div>
                  </td>
                  <td>
                    <div class="flex flex-wrap gap-1">
                      <code v-for="t in r.triggers" :key="t" class="cell-code break-all">{{ t }}</code>
                    </div>
                  </td>
                  <td>
                    <Badge v-if="r.severity === 'error'" color="red" :label="r.severity" />
                    <Badge v-else-if="r.severity === 'warn'" color="yellow" :label="r.severity" />
                    <span v-else class="text-slate-500">{{ r.severity }}</span>
                  </td>
                  <td v-if="data.group_by === 'layer'">
                    <router-link v-if="r.guide_kind === 'pattern'" :to="`/patterns/${r.guide}`"
                      class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">{{ r.guide }}</router-link>
                    <router-link v-else-if="r.guide_kind === 'auto'" :to="`/skills/${r.guide}`"
                      class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">{{ r.guide }}</router-link>
                    <span v-else class="text-slate-500">{{ r.guide }}</span>
                  </td>
                  <td v-else><code class="cell-code">{{ r.layer }}</code></td>
                  <td class="text-slate-600">{{ r.summary }}</td>
                </tr>
              </tbody>
            </table>
          </Card>
        </div>
      </div>
    </div>

    <!-- Scripts tab -->
    <div v-else-if="activeTab === 'scripts'">
      <div v-if="!scriptsData" class="empty-state">Loading scripts…</div>
      <div v-else>
        <p class="page-subtitle mb-4">
          Runnable helpers shipped inside each pattern's <code>scripts/</code> directory.
          Patterns that declare GritQL rules also bundle the shared runner scripts
          (<code>check_patterns.sh</code>, <code>find_applicable_files.py</code>) at
          <em>pattern promote</em> time so the deployed skill can invoke its own rules.
        </p>

        <Card v-if="!scriptsData.patterns.length" class="empty-state">
          No pattern has any bundled scripts.
        </Card>

        <div v-for="p in scriptsData.patterns" :key="p.slug" class="mb-5">
          <Card :no-padding="true">
            <div class="card-group-header flex items-center justify-between">
              <h2 class="card-group-title">
                <router-link :to="`/patterns/${p.slug}`"
                  class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">{{ p.title }}</router-link>
                <code class="cell-code ml-2">{{ p.slug }}</code>
              </h2>
              <div class="flex gap-1 items-center">
                <Badge v-if="p.has_grit_rules" color="blue" label="+ runner scripts on promote" />
                <span class="text-xs text-slate-400 ml-2 font-mono">
                  {{ p.own_scripts.length }} script{{ p.own_scripts.length !== 1 ? 's' : '' }}
                </span>
              </div>
            </div>
            <table class="tbl">
              <thead>
                <tr><th>File</th><th>Language</th><th>Size</th></tr>
              </thead>
              <tbody>
                <tr v-for="s in p.own_scripts" :key="s.name">
                  <td><code class="cell-code break-all">{{ s.name }}</code></td>
                  <td><Badge color="gray" :label="s.language" /></td>
                  <td class="text-slate-500 text-xs font-mono">{{ fmtSize(s.size) }}</td>
                </tr>
              </tbody>
            </table>
          </Card>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.seg-count {
    font-size: 0.6875rem;
    color: inherit;
    opacity: 0.7;
    margin-left: 0.25rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.filter-row {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-wrap: wrap;
}

.filter-row-label {
    font-size: 0.75rem;
    color: #64748B;
    margin-right: 0.25rem;
}

.filter-select {
    width: auto;
    font-size: 0.75rem;
    padding: 0.25rem 1.75rem 0.25rem 0.5rem;
    min-width: 9rem;
    max-width: 14rem;
}

/* Rules wrap to multiple lines (id, stacked engine/scope badges, triggers,
   summary), so top-align cells instead of the global .tbl middle default. */
.tbl td {
    vertical-align: top;
}
</style>
