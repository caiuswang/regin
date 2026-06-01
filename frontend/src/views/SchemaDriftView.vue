<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import { useConfirm } from '../composables/useConfirm'
import { useDiagnosticsState } from '../composables/useDiagnosticsState'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import StatCard from '../components/StatCard.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import DiffBlock from '../components/DiffBlock.vue'

const { confirm } = useConfirm()
const { enabled: diagEnabled, setEnabled: setDiag } = useDiagnosticsState()

async function toggleDiagnostics() {
  try { await setDiag(!diagEnabled.value) }
  catch (e) { error.value = e.message || String(e) }
}

const schemas = ref([])
const kpi = ref({ total: 0, clean: 0, drift: 0, overlaid: 0, pending_findings: 0 })
const allAgents = ref([])
const driftsBySchema = ref({})          // `${agent}::${tool}` -> drift rows
const schemaBySchema = ref({})          // `${agent}::${tool}` -> merged schema doc
const diffBySchema = ref({})            // `${agent}::${tool}` -> {current, proposed, unified_diff}
const tabBySchema = ref({})             // `${agent}::${tool}` -> 'schema' | 'diff' | 'findings'
const detailById = ref({})              // drift_id -> detail payload
const detailLoading = ref({})           // drift_id -> bool
const expandedSchema = ref(null)        // `${agent}::${tool}`
const expandedDrift = ref(null)         // drift_id

const search = ref('')
const stateFilter = ref('all')
const agentFilter = ref('')
const loading = ref(false)
const error = ref('')

const STATE_COLOR = { clean: 'green', drift: 'yellow', overlaid: 'blue' }
const STATE_LABEL = { clean: 'clean', drift: 'has drift', overlaid: 'overlaid' }

const KIND_COLOR = {
  unknown_field: 'blue', missing_required: 'red',
  type_mismatch: 'yellow', enum_violation: 'yellow', unknown_tool: 'gray',
  unknown_event: 'gray',
}
const KIND_LABEL = {
  unknown_field: 'unknown field', missing_required: 'missing required',
  type_mismatch: 'type mismatch', enum_violation: 'enum violation',
  unknown_tool: 'unknown tool', unknown_event: 'unknown event',
}

const stateChips = [
  { value: 'all',      label: 'All' },
  { value: 'drift',    label: 'Has drift' },
  { value: 'clean',    label: 'Clean' },
  { value: 'overlaid', label: 'Overlaid' },
]

// Tools vs hook events are separate subject_kind axes sharing the same
// table/UI. Default to tools so the page opens to its historical view.
const kindFilter = ref('tool')
const kindChips = [
  { value: 'tool',       label: 'Tools' },
  { value: 'hook_event', label: 'Hooks' },
]

const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  return schemas.value.filter(s => {
    if (agentFilter.value && s.agent !== agentFilter.value) return false
    if (stateFilter.value !== 'all' && s.state !== stateFilter.value) return false
    if (term && !s.tool.toLowerCase().includes(term)) return false
    return true
  })
})

const showAgentColumn = computed(() => allAgents.value.length > 1)

// Key on subject_kind too: a tool and a hook event can share a name
// (e.g. nothing stops a future tool called `Stop`), so the kind must
// disambiguate the row's cached schema / drift / diff sub-docs.
function schemaKey(s) { return `${s.agent}::${s.subject_kind}::${s.tool}` }

async function loadSchemas() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ kind: kindFilter.value })
    if (agentFilter.value) params.set('agent', agentFilter.value)
    const resp = await api.get(`/schema-drift/schemas?${params}`)
    schemas.value = resp.rows || []
    kpi.value = resp.kpi || kpi.value
    allAgents.value = resp.agents || []
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

function setKind(kind) {
  if (kindFilter.value === kind) return
  kindFilter.value = kind
  expandedSchema.value = null
  expandedDrift.value = null
  loadSchemas()
}

async function toggleSchema(s) {
  const key = schemaKey(s)
  if (expandedSchema.value === key) {
    expandedSchema.value = null
    expandedDrift.value = null
    return
  }
  expandedSchema.value = key
  expandedDrift.value = null
  // Default tab: Diff if there's drift to preview, otherwise Schema.
  const defaultTab = tabBySchema.value[key] || (s.pending > 0 ? 'diff' : 'schema')
  if (!tabBySchema.value[key]) {
    tabBySchema.value = { ...tabBySchema.value, [key]: defaultTab }
  }
  // Fetch schema body + drift list (and the diff, if that's the default
  // tab) in parallel. Without the diff prefetch here, opening a drifty
  // schema would land on the Diff tab but never trigger its fetch —
  // setTab is only wired to user clicks.
  const needsSchema = !schemaBySchema.value[key]
  const needsDrifts = !driftsBySchema.value[key]
  const needsDiff = defaultTab === 'diff' && !diffBySchema.value[key]
  try {
    const tasks = []
    if (needsSchema) {
      tasks.push(api.get(
        `/schema-drift/schema?${schemaQuery(s)}`,
      ).then(d => { schemaBySchema.value = { ...schemaBySchema.value, [key]: d } }))
    }
    if (needsDrifts) {
      tasks.push(api.get(
        `/schema-drift?agent=${encodeURIComponent(s.agent)}&status=pending&kind=${s.subject_kind}`,
      ).then(resp => {
        const forTool = (resp.rows || []).filter(
          r => r.tool_name === s.tool && r.subject_kind === s.subject_kind,
        )
        driftsBySchema.value = { ...driftsBySchema.value, [key]: forTool }
      }))
    }
    if (needsDiff) {
      tasks.push(api.get(
        `/schema-drift/schema/diff?${schemaQuery(s)}`,
      ).then(d => { diffBySchema.value = { ...diffBySchema.value, [key]: d } }))
    }
    await Promise.all(tasks)
  } catch (e) {
    error.value = e.message || String(e)
  }
}

// agent + tool + kind query shared by the schema and diff endpoints
// (both default kind to 'tool' server-side, so hook rows must pass it).
function schemaQuery(s) {
  return new URLSearchParams({
    agent: s.agent, tool: s.tool, kind: s.subject_kind,
  }).toString()
}

async function setTab(s, tab) {
  const key = schemaKey(s)
  tabBySchema.value = { ...tabBySchema.value, [key]: tab }
  if (tab === 'diff' && !diffBySchema.value[key]) {
    try {
      const d = await api.get(`/schema-drift/schema/diff?${schemaQuery(s)}`)
      diffBySchema.value = { ...diffBySchema.value, [key]: d }
    } catch (e) {
      error.value = e.message || String(e)
    }
  }
}

function activeTab(s) {
  return tabBySchema.value[schemaKey(s)] || 'schema'
}

async function toggleDrift(r) {
  if (expandedDrift.value === r.id) {
    expandedDrift.value = null
    return
  }
  expandedDrift.value = r.id
  if (detailById.value[r.id]) return
  detailLoading.value = { ...detailLoading.value, [r.id]: true }
  try {
    const d = await api.get(`/schema-drift/${r.id}/detail`)
    detailById.value = { ...detailById.value, [r.id]: d }
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    detailLoading.value = { ...detailLoading.value, [r.id]: false }
  }
}

async function ratify(r) {
  try {
    await api.post(`/schema-drift/${r.id}/ratify`, {})
    await refreshOpenSchema(r)
  } catch (e) { error.value = e.message || String(e) }
}

async function ignore(r) {
  try {
    await api.post(`/schema-drift/${r.id}/ignore`, {})
    await refreshOpenSchema(r)
  } catch (e) { error.value = e.message || String(e) }
}

async function discard(r) {
  const ok = await confirm('Discard finding?',
    `${r.field_path} — will reappear if seen again.`, true)
  if (!ok) return
  try {
    await api.delete(`/schema-drift/${r.id}`)
    await refreshOpenSchema(r)
  } catch (e) { error.value = e.message || String(e) }
}

async function refreshOpenSchema(r) {
  const key = `${r.agent}::${r.subject_kind}::${r.tool_name}`
  // Invalidate every cached sub-doc so re-expand re-fetches schema +
  // drift list + diff (ratify mutated the overlay).
  delete driftsBySchema.value[key]
  delete schemaBySchema.value[key]
  delete diffBySchema.value[key]
  await loadSchemas()
  expandedSchema.value = null
  const s = schemas.value.find(x => schemaKey(x) === key)
  if (s) await toggleSchema(s)
}

function fmtSample(s) {
  if (!s) return ''
  return s.length > 80 ? s.slice(0, 80) + '…' : s
}

function pretty(obj) {
  if (obj === null || obj === undefined) return ''
  return JSON.stringify(obj, null, 2)
}

function relevantSubschema(detail) {
  if (!detail?.schema || !detail?.drift?.field_path) return null
  const parts = detail.drift.field_path.split('.').filter(Boolean)
  let node = detail.schema
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i].replace(/\[\d+\]$/, '')
    if (!node?.properties?.[part]) return node
    node = node.properties[part]
    if (node?.items) node = node.items
  }
  return node
}

onMounted(loadSchemas)
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Diagnostics</div>
        <h1 class="page-title">Schema drift</h1>
        <p class="page-subtitle">
          Live payload fields that don't appear in the published schema — for
          tool calls and hook events alike. Ratify to add them to your local overlay.
        </p>
      </div>
      <div class="page-actions">
        <ToggleSwitch
          :model-value="diagEnabled"
          @change="toggleDiagnostics"
          on-label="Diagnostics on"
          off-label="Diagnostics off"
        />
      </div>
    </header>

    <div v-if="!diagEnabled" class="banner banner-warn">
      <Badge color="yellow" label="off" />
      Diagnostics is off — no new drift will be recorded. Rows below are historical.
    </div>

    <div class="segmented kind-switch" role="tablist" aria-label="Subject kind">
      <button
        v-for="k in kindChips" :key="k.value"
        type="button"
        role="tab"
        :aria-selected="kindFilter === k.value"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': kindFilter === k.value }"
        @click="setKind(k.value)"
      >{{ k.label }}</button>
    </div>

    <div class="kpi-grid">
      <StatCard>
        <div class="stat-label">Schemas</div>
        <div class="stat-value">{{ kpi.total }}</div>
      </StatCard>
      <StatCard>
        <div class="stat-label">Clean</div>
        <div class="stat-value">{{ kpi.clean }}</div>
      </StatCard>
      <StatCard>
        <div class="stat-label">With drift</div>
        <div class="stat-value">{{ kpi.drift }}</div>
      </StatCard>
      <StatCard>
        <div class="stat-label">Overlaid</div>
        <div class="stat-value">{{ kpi.overlaid }}</div>
      </StatCard>
      <StatCard>
        <div class="stat-label">Pending findings</div>
        <div class="stat-value stat-value--with-tag">
          {{ kpi.pending_findings }}
          <Badge v-if="kpi.pending_findings > 0" color="yellow" label="pending" />
        </div>
      </StatCard>
    </div>

    <div class="toolbar">
      <div class="toolbar-search">
        <input
          type="search"
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"
          :placeholder="kindFilter === 'hook_event' ? 'Search event name…' : 'Search tool name…'"
          v-model="search"
        />
      </div>
      <template v-if="showAgentColumn">
        <span class="toolbar-label">Agent</span>
        <select
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"
          v-model="agentFilter"
          @change="loadSchemas"
          aria-label="Filter by agent"
        >
          <option value="">all</option>
          <option v-for="a in allAgents" :key="a" :value="a">{{ a }}</option>
        </select>
      </template>
      <span class="toolbar-count">{{ filtered.length }} schema<span v-if="filtered.length !== 1">s</span></span>
    </div>

    <div class="filter-row mb-4">
      <span class="filter-row-label">State:</span>
      <button
        v-for="c in stateChips" :key="c.value"
        type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: stateFilter === c.value }"
        @click="stateFilter = c.value"
      >{{ c.label }}</button>
    </div>

    <p v-if="error" class="banner banner-error">{{ error }}</p>

    <div v-if="loading" class="empty-state">Loading…</div>
    <div v-else-if="!filtered.length" class="empty-state">No schemas match.</div>

    <Card v-else :no-padding="true">
      <table class="tbl schemas-tbl">
        <thead>
          <tr>
            <th class="caret-col"></th>
            <th>Schema</th>
            <th v-if="showAgentColumn">Agent</th>
            <th>State</th>
            <th class="text-right">Pending</th>
            <th class="text-right">Ratified</th>
            <th>Overlay</th>
            <th>Last drift</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in filtered" :key="schemaKey(s)">
            <tr
              class="row-clickable focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="{ 'tbl-row-active': expandedSchema === schemaKey(s) }"
              tabindex="0"
              @click="toggleSchema(s)"
              @keydown.enter.prevent="toggleSchema(s)"
              @keydown.space.prevent="toggleSchema(s)"
            >
              <td class="caret-col">
                <span class="caret" :class="{ open: expandedSchema === schemaKey(s) }">▸</span>
              </td>
              <td><code class="cell-code">{{ s.tool }}</code></td>
              <td v-if="showAgentColumn"><code class="cell-code">{{ s.agent }}</code></td>
              <td>
                <Badge :color="STATE_COLOR[s.state] || 'gray'" :label="STATE_LABEL[s.state] || s.state" />
              </td>
              <td class="text-right tabular">{{ s.pending || '—' }}</td>
              <td class="text-right tabular text-muted">{{ s.ratified || '—' }}</td>
              <td class="text-muted">{{ s.overlay ? 'yes' : '—' }}</td>
              <td class="text-muted text-xs">{{ s.last_drift_seen || '—' }}</td>
            </tr>

            <tr v-if="expandedSchema === schemaKey(s)" class="expansion-row">
              <td :colspan="showAgentColumn ? 8 : 7">
                <div class="expansion">
                  <div class="segmented">
                    <button
                      type="button"
                      class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
                      :class="{ 'is-active': activeTab(s) === 'schema' }"
                      @click.stop="setTab(s, 'schema')"
                    >Schema</button>
                    <button
                      v-if="s.pending > 0"
                      type="button"
                      class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
                      :class="{ 'is-active': activeTab(s) === 'diff' }"
                      @click.stop="setTab(s, 'diff')"
                    >
                      Diff
                      <span class="seg-count">{{ s.pending }}</span>
                    </button>
                    <button
                      type="button"
                      class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
                      :class="{ 'is-active': activeTab(s) === 'findings' }"
                      @click.stop="setTab(s, 'findings')"
                    >
                      Findings
                      <span v-if="s.pending" class="seg-count">{{ s.pending }}</span>
                    </button>
                  </div>

                  <!-- Schema tab -->
                  <div v-if="activeTab(s) === 'schema'" class="tab-panel">
                    <div v-if="!schemaBySchema[schemaKey(s)]" class="empty-state-inline">Loading schema…</div>
                    <div v-else>
                      <dl class="meta-kv">
                        <dt>Baseline</dt>
                        <dd><code class="cell-code">{{ schemaBySchema[schemaKey(s)].baseline_path }}</code></dd>
                        <dt>Overlay</dt>
                        <dd class="meta-kv__overlay">
                          <code class="cell-code">{{ schemaBySchema[schemaKey(s)].overlay_path }}</code>
                          <Badge
                            v-if="!schemaBySchema[schemaKey(s)].overlay_exists"
                            color="gray" label="not yet"
                          />
                          <Badge v-else color="blue" label="overlay active" />
                        </dd>
                      </dl>
                      <pre class="code-block">{{ pretty(schemaBySchema[schemaKey(s)].schema) }}</pre>
                    </div>
                  </div>

                  <!-- Diff tab -->
                  <div v-else-if="activeTab(s) === 'diff'" class="tab-panel">
                    <div v-if="!diffBySchema[schemaKey(s)]" class="empty-state-inline">Loading diff…</div>
                    <div v-else>
                      <p class="tab-help">
                        Preview the overlay after ratifying every pending unknown_field for this schema
                        ({{ diffBySchema[schemaKey(s)].pending_count }} change<span v-if="diffBySchema[schemaKey(s)].pending_count !== 1">s</span>).
                      </p>
                      <div v-if="diffBySchema[schemaKey(s)].unified_diff" class="diff-wrap">
                        <DiffBlock
                          :diff="diffBySchema[schemaKey(s)].unified_diff"
                          :file-path="`${s.tool}.json`"
                        />
                      </div>
                      <p v-else class="empty-state-inline">
                        No textual change — schema already covers every pending field.
                      </p>
                    </div>
                  </div>

                  <!-- Findings tab -->
                  <div v-else class="tab-panel tab-panel--flush">
                    <div v-if="!driftsBySchema[schemaKey(s)]" class="empty-state-inline pad">
                      Loading findings…
                    </div>
                    <div v-else-if="!driftsBySchema[schemaKey(s)].length" class="empty-state-inline pad">
                      No pending drift findings — this schema matches every live payload so far.
                    </div>
                    <table v-else class="tbl findings-tbl">
                      <thead>
                        <tr>
                          <th class="caret-col"></th>
                          <th>Field</th>
                          <th>Kind</th>
                          <th>Sample</th>
                          <th>Version</th>
                          <th class="text-right">Count</th>
                          <th class="actions-col">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        <template v-for="r in driftsBySchema[schemaKey(s)]" :key="r.id">
                          <tr
                            class="row-clickable focus-visible:outline-2 focus-visible:outline-blue-500"
                            :class="{ 'tbl-row-active': expandedDrift === r.id }"
                            tabindex="0"
                            @click.stop="toggleDrift(r)"
                            @keydown.enter.prevent.stop="toggleDrift(r)"
                            @keydown.space.prevent.stop="toggleDrift(r)"
                          >
                            <td class="caret-col">
                              <span class="caret" :class="{ open: expandedDrift === r.id }">▸</span>
                            </td>
                            <td><code class="cell-code">{{ r.field_path }}</code></td>
                            <td>
                              <Badge
                                :color="KIND_COLOR[r.drift_kind] || 'gray'"
                                :label="KIND_LABEL[r.drift_kind] || r.drift_kind"
                              />
                            </td>
                            <td><code class="cell-code sample">{{ fmtSample(r.sample_value) }}</code></td>
                            <td><code class="cell-code">{{ r.claude_version || '—' }}</code></td>
                            <td class="text-right tabular">{{ r.occurrence_count }}</td>
                            <td class="actions-col">
                              <button
                                v-if="r.drift_kind === 'unknown_field'"
                                type="button"
                                class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
                                title="Add this field to your local overlay"
                                @click.stop="ratify(r)"
                              >Ratify</button>
                              <button
                                type="button"
                                class="btn btn-ghost focus-visible:outline-2 focus-visible:outline-blue-500"
                                @click.stop="ignore(r)"
                              >Ignore</button>
                              <button
                                type="button"
                                class="btn btn-ghost btn-danger focus-visible:outline-2 focus-visible:outline-blue-500"
                                @click.stop="discard(r)"
                              >Discard</button>
                            </td>
                          </tr>

                          <tr v-if="expandedDrift === r.id" class="expansion-row expansion-row--inner">
                            <td colspan="7">
                              <div v-if="detailLoading[r.id]" class="empty-state-inline pad">
                                Loading detail…
                              </div>
                              <div v-else-if="detailById[r.id]" class="detail-grid">
                                <section>
                                  <div class="detail-heading">Schema</div>
                                  <dl class="meta-kv">
                                    <dt>Baseline</dt>
                                    <dd><code class="cell-code">{{ detailById[r.id].baseline_path }}</code></dd>
                                    <dt>Overlay</dt>
                                    <dd class="meta-kv__overlay">
                                      <code class="cell-code">{{ detailById[r.id].overlay_path }}</code>
                                      <Badge
                                        v-if="!detailById[r.id].overlay_exists"
                                        color="gray" label="not yet"
                                      />
                                    </dd>
                                  </dl>
                                </section>
                                <section>
                                  <div class="detail-heading">Current schema (slice)</div>
                                  <pre class="code-block">{{ pretty(relevantSubschema(detailById[r.id])) }}</pre>
                                </section>
                                <section v-if="detailById[r.id].proposed_change">
                                  <div class="detail-heading">Ratify would add</div>
                                  <pre class="code-block code-block-add">{{ pretty({ [detailById[r.id].proposed_change.leaf]: detailById[r.id].proposed_change.schema_to_insert }) }}</pre>
                                </section>
                                <section>
                                  <div class="detail-heading">Raw payload</div>
                                  <p v-if="!detailById[r.id].payload" class="empty-state-inline">
                                    Not in <code class="cell-code">~/.claude/hook-payloads.jsonl</code> (rotated past).
                                  </p>
                                  <pre v-else class="code-block">{{ pretty(detailById[r.id].payload) }}</pre>
                                </section>
                              </div>
                            </td>
                          </tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </Card>
  </div>
</template>

<style scoped>
/* Layout — KPI strip + banners ----------------------------------- */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.stat-value--with-tag {
  display: inline-flex;
  align-items: baseline;
  gap: 0.5rem;
}

.banner {
  display: flex; align-items: center; gap: 0.625rem;
  padding: 0.5rem 0.875rem;
  border-radius: 0.5rem;
  font-size: 0.8125rem;
  margin-bottom: 1rem;
}
.banner-warn { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; }
.banner-error { background: #FEF2F2; color: #991B1B; border: 1px solid #FECACA; }

/* Kind switch (Tools | Hooks) — primary axis above the KPI strip. */
.kind-switch {
  display: inline-flex;
  margin-bottom: 1rem;
}

/* Toolbar -------------------------------------------------------- */
.toolbar-search .input { width: 18rem; max-width: 100%; }

/* Tables — column widths so expanded JSON can't reflow header cols. */
.schemas-tbl, .findings-tbl { table-layout: fixed; }
.caret-col { width: 1.5rem; }
.actions-col { text-align: right; white-space: nowrap; width: 14rem; }

/* Direct-child selectors so these widths don't bleed into the
   findings-tbl that's nested inside an expansion row — without `>`
   the rules below match BOTH tables' th elements and squeeze the
   inner Actions column down to 7rem. */
.schemas-tbl > thead > tr > th:nth-child(3),
.schemas-tbl > thead > tr > th:nth-child(4),
.schemas-tbl > thead > tr > th:nth-child(5),
.schemas-tbl > thead > tr > th:nth-child(6),
.schemas-tbl > thead > tr > th:nth-child(7) { width: 7rem; }
.schemas-tbl > thead > tr > th:nth-child(8) { width: 11rem; }

/* Findings-tbl column widths (Field auto-fills the leftover). */
.findings-tbl > thead > tr > th:nth-child(3) { width: 9rem; }   /* Kind */
.findings-tbl > thead > tr > th:nth-child(5) { width: 6rem; }   /* Version */
.findings-tbl > thead > tr > th:nth-child(6) { width: 5rem; }   /* Count */

.row-clickable { cursor: pointer; }

.caret {
  color: #94A3B8; font-size: 0.75rem;
  transition: transform 120ms ease;
  display: inline-block;
}
.caret.open { transform: rotate(90deg); color: #1E40AF; }

.tabular { font-variant-numeric: tabular-nums; }
.text-right { text-align: right; }
.text-muted { color: #94A3B8; }

.sample { color: #475569; word-break: break-all; }

/* Expansion panel ------------------------------------------------- */
.expansion-row > td {
  background: #F8FAFC;
  padding: 0 !important;
  border-bottom: 1px solid #E2E8F0;
}
.expansion-row:hover { background: transparent; }
.expansion {
  padding: 0.875rem 1.125rem;
  display: flex; flex-direction: column; gap: 0.875rem;
}
.tab-panel { display: flex; flex-direction: column; gap: 0.625rem; }
.tab-panel--flush { padding: 0; gap: 0; }
.tab-help { font-size: 0.8125rem; color: #475569; line-height: 1.5; margin: 0; }
.diff-wrap {
  background: #0F172A;
  border-radius: 0.5rem;
  overflow: hidden;
}

.seg-count {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 1.25rem; height: 1.125rem;
  padding: 0 0.375rem; margin-left: 0.375rem;
  background: rgba(148, 163, 184, 0.18);
  border-radius: 999px;
  font-size: 0.6875rem; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.segmented-item.is-active .seg-count {
  background: rgba(30, 64, 175, 0.12);
  color: #1E40AF;
}

/* Findings table (nested inside the expansion) -------------------- */
.findings-tbl {
  background: #fff;
  border: 1px solid #E2E8F0;
  border-radius: 0.625rem;
  font-size: 0.8125rem;
}
.findings-tbl thead { background: #F8FAFC; }
.expansion-row--inner > td {
  background: #FAFCFE;
  border-top: 1px solid #E2E8F0;
}

/* Schema/detail key-value rows ------------------------------------ */
.meta-kv {
  display: grid;
  grid-template-columns: 6rem 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
  font-size: 0.8125rem;
}
.meta-kv dt { color: #94A3B8; font-weight: 500; }
.meta-kv dd { margin: 0; color: #1E293B; min-width: 0; }
.meta-kv__overlay { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }

/* Code blocks — dark only because they show code/JSON.
   Everything else on the page stays neutral. */
.code-block {
  background: #0F172A; color: #E2E8F0;
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem; line-height: 1.55;
  max-height: 22rem;
  overflow: auto; margin: 0;
  white-space: pre;
}
.code-block-add {
  background: #052E2A;
  color: #BBF7D0;
  box-shadow: inset 3px 0 0 #10B981;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
  padding: 0.875rem 1.125rem;
}
.detail-heading {
  font-size: 0.6875rem; font-weight: 600;
  color: #64748B;
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 0.375rem;
}

.empty-state-inline {
  font-size: 0.8125rem;
  color: #64748B;
  margin: 0;
}
.empty-state-inline.pad { padding: 0.75rem 0.25rem; }

/* Buttons -------------------------------------------------------- */
.btn {
  background: #fff;
  border: 1px solid #E2E8F0;
  color: #334155;
  font-size: 0.75rem; font-weight: 500;
  padding: 0.25rem 0.625rem;
  border-radius: 0.375rem;
  cursor: pointer;
  margin-left: 0.25rem;
  transition: background-color 120ms, border-color 120ms, color 120ms;
}
.btn:hover { background: #F8FAFC; border-color: #CBD5E1; }
.btn-primary {
  background: #1E40AF; color: #fff; border-color: #1E40AF;
}
.btn-primary:hover { background: #1E3A8A; border-color: #1E3A8A; }
.btn-ghost {
  background: transparent; border-color: transparent; color: #475569;
}
.btn-ghost:hover { background: #F1F5F9; border-color: #E2E8F0; }
.btn-ghost.btn-danger { color: #B91C1C; }
.btn-ghost.btn-danger:hover { background: #FEF2F2; border-color: #FECACA; }
</style>
