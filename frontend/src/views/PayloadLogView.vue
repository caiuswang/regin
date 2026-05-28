<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import { useDiagnosticsState } from '../composables/useDiagnosticsState'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import StatCard from '../components/StatCard.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'

const { enabled: diagEnabled, setEnabled: setDiag } = useDiagnosticsState()

const entries = ref([])
const meta = ref({ exists: false, path: '', size_bytes: 0, returned: 0 })
const eventFilter = ref('PostToolUse')
const toolFilter = ref('')
const limit = ref(50)
const loading = ref(false)
const error = ref('')
const expandedIdx = ref(null)

const EVENT_OPTIONS = [
  '', 'PostToolUse', 'PreToolUse', 'UserPromptSubmit', 'SessionStart',
  'SessionEnd', 'SubagentStart', 'SubagentStop', 'PermissionRequest',
]

const fileSizeMB = computed(() => (meta.value.size_bytes / 1024 / 1024).toFixed(1))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
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
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
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
        <select
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"
          v-model="eventFilter" @change="load"
        >
          <option v-for="ev in EVENT_OPTIONS" :key="ev" :value="ev">{{ ev || 'All' }}</option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">Tool</span>
        <input
          type="search" placeholder="exact name…"
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"
          v-model="toolFilter" @change="load"
        />
      </label>
      <label class="field">
        <span class="field-label">Limit</span>
        <select
          class="input focus-visible:outline-2 focus-visible:outline-blue-500"
          v-model.number="limit" @change="load"
        >
          <option :value="50">50</option>
          <option :value="200">200</option>
          <option :value="500">500</option>
          <option :value="1000">1000</option>
        </select>
      </label>
      <button
        type="button"
        class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="load"
      >Refresh</button>
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
                <code v-if="e.session_id" class="cell-code" :title="e.session_id">{{ e.session_id }}</code>
                <span v-else class="text-muted">—</span>
              </td>
            </tr>
            <tr v-if="expandedIdx === i" class="expansion-row">
              <td colspan="5">
                <pre class="code-block">{{ pretty(e.payload) }}</pre>
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
  color: #94A3B8;
  font-weight: 500;
  margin-left: 0.125rem;
}
.kpi-path { min-width: 0; }
.kpi-path-value {
  font-size: 0.8125rem;
  word-break: break-all;
  color: #1E293B;
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
.banner-warn { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; }
.banner-error { background: #FEF2F2; color: #991B1B; border: 1px solid #FECACA; }

.field {
  display: inline-flex;
  /* Baseline (not center) so the small-caps label sits on the
     input's text baseline instead of floating above the input box. */
  align-items: baseline;
  gap: 0.5rem;
}
.field-label {
  font-size: 0.75rem; color: #64748B;
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
.log-tbl { table-layout: fixed; }
.caret-col { width: 1.5rem; }
.log-tbl > thead > tr > th:nth-child(2) { width: 11rem; }   /* Time */
.log-tbl > thead > tr > th:nth-child(3) { width: 10rem; }   /* Event */
.log-tbl > thead > tr > th:nth-child(4) { width: 12rem; }   /* Tool */
/* Session: auto leftover */

.row-clickable { cursor: pointer; }

.caret {
  color: #94A3B8; font-size: 0.75rem;
  transition: transform 120ms ease;
  display: inline-block;
}
.caret.open { transform: rotate(90deg); color: #1E40AF; }

.text-muted { color: #94A3B8; }

.expansion-row > td {
  background: #F8FAFC;
  padding: 0.75rem 1rem !important;
  border-bottom: 1px solid #E2E8F0;
}
.expansion-row:hover { background: transparent; }

.code-block {
  background: #0F172A; color: #E2E8F0;
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem; line-height: 1.55;
  max-height: 30rem;
  overflow: auto; margin: 0;
  white-space: pre;
}

.btn {
  background: #fff;
  border: 1px solid #E2E8F0;
  color: #334155;
  font-size: 0.75rem; font-weight: 500;
  padding: 0.3125rem 0.75rem;
  border-radius: 0.375rem;
  cursor: pointer;
  transition: background-color 120ms, border-color 120ms, color 120ms;
}
.btn:hover { background: #F8FAFC; border-color: #CBD5E1; }
.btn-primary {
  background: #1E40AF; color: #fff; border-color: #1E40AF;
}
.btn-primary:hover { background: #1E3A8A; border-color: #1E3A8A; }

@media (max-width: 800px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .kpi-path { grid-column: 1 / -1; }
}
</style>
