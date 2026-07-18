<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import { useDiagnosticsState } from '../composables/useDiagnosticsState'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import StatCard from '../components/StatCard.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import Button from '../components/ui/Button.vue'
import Input from '../components/ui/Input.vue'
import Select from '../components/ui/Select.vue'

const { enabled: diagEnabled, setEnabled: setDiag } = useDiagnosticsState()

const entries = ref([])
const meta = ref({ exists: false, path: '', size_bytes: 0, returned: 0 })
const eventFilter = ref('PostToolUse')
const toolFilter = ref('')
const limit = ref(50)
const loading = ref(false)
const error = ref('')
const expandedIdx = ref(null)

const allAgents = ref([])               // provider ids with a log to switch between
const agentFilter = ref('')             // selected provider id ('' until first load resolves it)
let didInit = false                     // first load adopts the server-resolved active agent

const EVENT_OPTIONS = [
  '', 'PostToolUse', 'PreToolUse', 'UserPromptSubmit', 'SessionStart',
  'SessionEnd', 'SubagentStart', 'SubagentStop', 'PermissionRequest',
]
const eventOptions = EVENT_OPTIONS.map(ev => ({ value: ev, label: ev || 'All' }))
const limitOptions = [50, 200, 500, 1000].map(n => ({ value: String(n), label: String(n) }))

const fileSizeMB = computed(() => (meta.value.size_bytes / 1024 / 1024).toFixed(1))

// One pill per provider that has a log. Unlike Schema drift (which holds
// every agent's rows in one fetch and filters client-side), each provider's
// payloads live in a separate file, so switching tabs re-hits the server.
const agentTabs = computed(() => allAgents.value.map(a => ({ value: a, label: a })))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (agentFilter.value) params.set('agent', agentFilter.value)
    if (eventFilter.value) params.set('event', eventFilter.value)
    if (toolFilter.value) params.set('tool', toolFilter.value)
    params.set('limit', String(limit.value))
    const resp = await api.get(`/diagnostics/payload-log?${params.toString()}`)
    entries.value = resp.entries || []
    meta.value = {
      exists: resp.exists,
      path: resp.path,
      size_bytes: resp.size_bytes || 0,
      returned: resp.returned || 0,
    }
    allAgents.value = resp.agents || []
    // Adopt the server-resolved active provider as the selected tab on the
    // first load, so the tab highlights the log we actually rendered.
    if (!didInit) {
      didInit = true
      if (resp.agent) agentFilter.value = resp.agent
    }
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

function setAgent(a) {
  if (agentFilter.value === a) return
  agentFilter.value = a
  expandedIdx.value = null
  load()
}

function toggle(idx) {
  expandedIdx.value = expandedIdx.value === idx ? null : idx
}

function pretty(obj) {
  if (obj === null || obj === undefined) return ''
  return JSON.stringify(obj, null, 2)
}

function fmtTime(s) {
  // received_at is an ISO-ish "2026-05-24T21:21:52.904714" — drop the
  // microseconds and the T separator so the column doesn't have to
  // carry 26 chars of monospace.
  if (!s) return '—'
  const noFrac = s.split('.')[0]
  return noFrac.replace('T', ' ')
}

async function toggleDiagnostics() {
  try {
    await setDiag(!diagEnabled.value)
  } catch (e) {
    error.value = e.message || String(e)
  }
}

onMounted(load)
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Diagnostics</div>
        <h1 class="page-title">Payload log</h1>
        <p class="page-subtitle">
          Raw hook payloads regin captured from Claude Code.
          Use this to inspect exactly what each event delivered.
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
      Diagnostics is off — new payloads aren't being logged. Entries below are historical.
    </div>

    <div
      v-if="agentTabs.length > 1"
      class="agent-tabs"
      role="tablist"
      aria-label="Agent"
    >
      <Button
        v-for="t in agentTabs" :key="t.value"
        size="sm"
        class="text-xs"
        role="tab"
        :aria-selected="agentFilter === t.value"
        :variant="agentFilter === t.value ? 'primary' : 'secondary'"
        @click="setAgent(t.value)"
      >{{ t.label }}</Button>
    </div>

    <div class="kpi-grid">
      <StatCard>
        <div class="stat-label">File size</div>
        <div class="stat-value">{{ fileSizeMB }}<span class="stat-unit"> MB</span></div>
      </StatCard>
      <StatCard>
        <div class="stat-label">Entries shown</div>
        <div class="stat-value">{{ meta.returned }}</div>
      </StatCard>
      <StatCard class="kpi-path">
        <div class="stat-label">File</div>
        <div v-if="meta.exists" class="kpi-path-value"><code class="cell-code">{{ meta.path }}</code></div>
        <div v-else class="kpi-path-value text-muted">
          No payload log yet — fires when Diagnostics is on and a hook arrives.
        </div>
      </StatCard>
    </div>

    <div class="toolbar">
      <label class="field">
        <span class="field-label">Event</span>
        <Select
          v-model="eventFilter"
          :options="eventOptions"
          @update:model-value="load"
        />
      </label>
      <label class="field">
        <span class="field-label">Tool</span>
        <Input
          type="search" placeholder="exact name…"
          class="focus-visible:outline-2 focus-visible:outline-blue-500"
          v-model="toolFilter" @change="load"
        />
      </label>
      <label class="field">
        <span class="field-label">Limit</span>
        <Select
          :model-value="String(limit)"
          :options="limitOptions"
          @update:model-value="v => { limit = Number(v); load() }"
        />
      </label>
      <Button variant="primary" @click="load">Refresh</Button>
      <span class="toolbar-count">{{ entries.length }} entr<span v-if="entries.length === 1">y</span><span v-else>ies</span></span>
    </div>

    <p v-if="error" class="banner banner-error">{{ error }}</p>

    <div v-if="loading" class="empty-state">Loading…</div>
    <div v-else-if="!entries.length && meta.exists" class="empty-state">
      No entries match the current filters.
    </div>
    <div v-else-if="!entries.length" class="empty-state">
      Payload log is empty. Enable Diagnostics and trigger any hook to start capturing.
    </div>

    <Card v-else :no-padding="true">
      <table class="tbl log-tbl">
        <thead>
          <tr>
            <th class="caret-col"></th>
            <th>Time</th>
            <th>Event</th>
            <th>Tool</th>
            <th>Session</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(e, i) in entries" :key="i">
            <tr
              class="row-clickable focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="{ 'tbl-row-active': expandedIdx === i }"
              tabindex="0"
              @click="toggle(i)"
              @keydown.enter.prevent="toggle(i)"
              @keydown.space.prevent="toggle(i)"
            >
              <td class="caret-col">
                <span class="caret" :class="{ open: expandedIdx === i }">▸</span>
              </td>
              <td><code class="cell-code">{{ fmtTime(e.received_at) }}</code></td>
              <td><Badge color="gray" :label="e.hook_event" /></td>
              <td class="cell-truncate">
                <code v-if="e.tool_name" class="cell-code" :title="e.tool_name">{{ e.tool_name }}</code>
                <span v-else class="text-muted">—</span>
              </td>
              <td class="cell-truncate">
                <code v-if="e.session_id" class="cell-code" :title="e.session_id">{{ e.session_id.slice(0, 8) }}</code>
                <span v-else class="text-muted">—</span>
              </td>
            </tr>
            <tr v-if="expandedIdx === i" class="expansion-row">
              <td colspan="5">
                <div class="expansion-clamp">
                  <pre class="code-block">{{ pretty(e.payload) }}</pre>
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
.kpi-grid {
  display: grid;
  grid-template-columns: 10rem 10rem 1fr;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.stat-unit {
  font-size: 0.875rem;
  color: var(--color-slate-400);
  font-weight: 500;
  margin-left: 0.125rem;
}
.kpi-path { min-width: 0; }
.kpi-path-value {
  font-size: 0.8125rem;
  word-break: break-all;
  color: var(--color-slate-800);
  line-height: 1.4;
  padding-top: 0.125rem;
}

.banner {
  display: flex; align-items: center; gap: 0.625rem;
  padding: 0.5rem 0.875rem;
  border-radius: 0.5rem;
  font-size: 0.8125rem;
  margin-bottom: 1rem;
}
.banner-warn { background: var(--color-amber-50); color: var(--color-amber-800); border: 1px solid var(--color-amber-200); }
.banner-error { background: var(--color-red-50); color: var(--color-red-800); border: 1px solid var(--color-red-200); }

/* Agent tabs (claude | kimi | …) — pill row that scopes the page to one
   provider's payload log. Mirrors the Schema drift agent tabs. */
.agent-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.field {
  display: inline-flex;
  /* Baseline (not center) so the small-caps label sits on the
     input's text baseline instead of floating above the input box. */
  align-items: baseline;
  gap: 0.5rem;
}
.field-label {
  font-size: 0.75rem; color: var(--color-slate-500);
  text-transform: uppercase; letter-spacing: 0.05em;
  font-weight: 500;
  line-height: 1;
}
.toolbar .input { padding: 0.25rem 0.5rem; font-size: 0.8125rem; }
.toolbar input[type="search"].input { width: 12rem; }
.toolbar select.input { min-width: 9rem; }

.cell-truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell-truncate .cell-code {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}

/* Table */
.log-tbl { table-layout: fixed; min-width: 40rem; }
.caret-col { width: 1.5rem; }
.log-tbl > thead > tr > th:nth-child(2) { width: 11rem; }   /* Time */
.log-tbl > thead > tr > th:nth-child(3) { width: 10rem; }   /* Event */
.log-tbl > thead > tr > th:nth-child(4) { width: 12rem; }   /* Tool */
/* Session: auto leftover */

.row-clickable { cursor: pointer; }

.caret {
  color: var(--color-slate-400); font-size: 0.75rem;
  transition: transform 120ms ease;
  display: inline-block;
}
.caret.open { transform: rotate(90deg); color: var(--color-blue-800); }

.text-muted { color: var(--color-slate-400); }

.expansion-row > td {
  background: var(--color-slate-50);
  padding: 0.75rem 1rem !important;
  border-bottom: 1px solid var(--color-slate-200);
}
.expansion-row:hover { background: transparent; }

.code-block {
  background: var(--code-bg); color: var(--code-fg);
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem; line-height: 1.55;
  max-height: 30rem;
  overflow: auto; margin: 0;
  white-space: pre;
}

/* Pin the payload to the scroller's visible box so JSON wraps at the
   viewport instead of the full table width on phones. */
.expansion-clamp {
  position: sticky;
  left: 0;
  max-width: calc(100vw - 3rem);
}

@media (max-width: 800px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .kpi-path { grid-column: 1 / -1; }
}
@media (max-width: 639px) {
  .code-block { white-space: pre-wrap; overflow-wrap: anywhere; }
}
</style>
