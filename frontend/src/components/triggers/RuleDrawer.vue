<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '../../api'
import SuppressButton from './SuppressButton.vue'

const props = defineProps({
  ruleId: { type: String, required: true },
  range: { type: String, default: '7d' },
  fallbackSeverity: { type: String, default: null },
  fallbackSource: { type: String, default: null },
})
const emit = defineEmits(['suppression-changed'])

const loading = ref(false)
const error = ref(null)
const detail = ref(null)

// Editor+ may mark events as noise. Viewers see suppressed metadata but
// can't change it. Same gate the backend enforces.
const currentUser = api.getStoredUser ? api.getStoredUser() : null
const canSuppress = computed(() => {
  const role = currentUser?.role
  return role === 'admin' || role === 'editor'
})

async function load() {
  loading.value = true
  error.value = null
  try {
    detail.value = await api.get(`/triggers/rules/${encodeURIComponent(props.ruleId)}?range=${encodeURIComponent(props.range)}`)
  } catch (e) {
    error.value = e?.message || 'Failed to load'
  } finally {
    loading.value = false
  }
}

async function onSuppressionChanged() {
  await load()
  emit('suppression-changed')
}

onMounted(load)
watch(() => [props.ruleId, props.range], load)

// Event-row deep link. `view=conversation` overrides the session-view
// localStorage default so users investigating a flagged trigger always
// land in the conversation, regardless of which tab they last used.
function sessionLink(ev) {
  if (!ev.session_id) return null
  const params = new URLSearchParams()
  if (ev.span_id) params.set('span', ev.span_id)
  if (ev.checked_at) params.set('t', ev.checked_at)
  params.set('view', 'conversation')
  return `/trace/sessions/${encodeURIComponent(ev.session_id)}?${params.toString()}`
}

// Top-sessions row link — same conversation-first behavior, no span anchor.
function topSessionLink(sid) {
  return `/trace/sessions/${encodeURIComponent(sid)}?view=conversation`
}
</script>

<template>
  <section class="rule-drawer" aria-label="Rule detail">
    <div v-if="loading" class="rule-drawer__loading">Loading detail…</div>
    <div v-else-if="error" class="rule-drawer__error">⚠ {{ error }}</div>
    <template v-else-if="detail">
      <!-- Full guide text -->
      <h3 class="rule-drawer__heading">Guide shown to the agent</h3>
      <pre v-if="detail.guide" class="rule-drawer__guide">{{ detail.guide }}</pre>
      <p v-else class="rule-drawer__empty">No guide text on record.</p>

      <!-- Two-column tables: files | sessions -->
      <div class="rule-drawer__grid">
        <div>
          <h3 class="rule-drawer__heading">Top files</h3>
          <table v-if="detail.files.length" class="tbl rule-drawer__table">
            <thead>
              <tr><th>File</th><th class="text-right">Checks</th><th class="text-right">Fires</th><th>Last</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in detail.files.slice(0, 10)" :key="f.file_path">
                <td><code class="cell-code block truncate" :title="f.file_path">{{ f.file_path }}</code></td>
                <td class="text-right font-mono text-xs">{{ f.checks }}</td>
                <td class="text-right">
                  <span v-if="f.fires > 0" class="badge badge-red">{{ f.fires }}</span>
                  <span v-else class="text-slate-400 font-mono text-xs">0</span>
                </td>
                <td class="text-slate-400 text-xs">{{ f.last_seen }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="rule-drawer__empty">No file activity in range.</p>
        </div>

        <div>
          <h3 class="rule-drawer__heading">Top sessions</h3>
          <table v-if="detail.sessions.length" class="tbl rule-drawer__table">
            <thead>
              <tr><th>Session</th><th class="text-right">Checks</th><th class="text-right">Fires</th><th>Last</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in detail.sessions.slice(0, 10)" :key="s.session_id || 'unknown'">
                <td>
                  <router-link v-if="s.session_id" :to="topSessionLink(s.session_id)"
                               class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                    <code class="cell-code">{{ s.session_id.slice(0, 8) }}…</code>
                  </router-link>
                  <span v-else class="text-slate-400 text-xs">unknown</span>
                </td>
                <td class="text-right font-mono text-xs">{{ s.checks }}</td>
                <td class="text-right">
                  <span v-if="s.fires > 0" class="badge badge-red">{{ s.fires }}</span>
                  <span v-else class="text-slate-400 font-mono text-xs">0</span>
                </td>
                <td class="text-slate-400 text-xs">{{ s.last_seen }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="rule-drawer__empty">No session activity in range.</p>
        </div>
      </div>

      <!-- Recent matched events. The full check log (matches + misses)
           lives in /trace/triggers/raw with ?rule=<id> — this list
           narrows to the rows where the rule actually fired plus any
           the user has flagged as noise, so triage can happen in place. -->
      <h3 class="rule-drawer__heading">
        Recent matched events
        <span class="rule-drawer__heading-hint">click → jumps to the exact span in conversation view</span>
      </h3>
      <table v-if="detail.events.length" class="tbl rule-drawer__table">
        <thead>
          <tr>
            <th>When</th>
            <th>File</th>
            <th>Session</th>
            <th class="text-right">Matches</th>
            <th aria-label="Mark as noise"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in detail.events" :key="ev.id"
              :class="{ 'rule-drawer__event--suppressed': ev.suppressed }">
            <td class="text-slate-400 text-xs whitespace-nowrap">{{ ev.checked_at }}</td>
            <td><code class="cell-code block truncate" :title="ev.file_path">{{ ev.file_path }}</code></td>
            <td>
              <router-link v-if="sessionLink(ev)" :to="sessionLink(ev)"
                           class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                <code class="cell-code">{{ ev.session_id.slice(0, 8) }}…</code>
              </router-link>
              <span v-else class="text-slate-300 text-xs">-</span>
            </td>
            <td class="text-right">
              <span v-if="ev.suppressed" class="badge badge-gray" :title="ev.suppression?.reason || 'no reason given'">
                noise · {{ ev.suppression?.suppressed_by_username || 'unknown' }}
              </span>
              <span v-else-if="ev.triggered" class="badge badge-red">{{ ev.match_count }}</span>
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </td>
            <td class="text-right">
              <SuppressButton
                v-if="canSuppress"
                :trigger-id="ev.id"
                :suppressed="ev.suppressed"
                :enabled="!!ev.triggered"
                @changed="onSuppressionChanged"
              />
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="rule-drawer__empty">No events in range.</p>

      <!-- Actions -->
      <div class="rule-drawer__actions">
        <router-link :to="`/rules/${encodeURIComponent(ruleId)}`" class="btn btn-primary text-xs">
          Edit rule →
        </router-link>
        <router-link :to="`/trace/triggers/raw?rule=${encodeURIComponent(ruleId)}`" class="btn btn-secondary text-xs">
          View raw events
        </router-link>
      </div>
    </template>
  </section>
</template>

<style scoped>
.rule-drawer {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #e2e8f0;
}
.rule-drawer__heading {
  font-size: 12px;
  font-weight: 600;
  color: #1e40af;
  margin: 0 0 8px 0;
  text-transform: none;
}
.rule-drawer__heading-hint {
  font-weight: 400;
  font-size: 11px;
  color: #94a3b8;
  margin-left: 8px;
}
.rule-drawer__guide {
  background: #1e293b;
  color: #a7f3d0;
  border-radius: 4px;
  padding: 12px 14px;
  font-size: 12px;
  margin: 0 0 16px 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.rule-drawer__grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 24px;
  margin-bottom: 16px;
}
@media (min-width: 768px) {
  .rule-drawer__grid { grid-template-columns: 1fr 1fr; }
}
.rule-drawer__table { margin-bottom: 4px; }
.rule-drawer__empty {
  font-size: 12px;
  color: #94a3b8;
  font-style: italic;
  margin: 4px 0 16px 0;
}
.rule-drawer__loading,
.rule-drawer__error {
  font-size: 12px;
  color: #64748b;
  padding: 8px 0;
}
.rule-drawer__error { color: #b91c1c; }
.rule-drawer__actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.rule-drawer__event--suppressed > td {
  color: #94a3b8;
  text-decoration: line-through;
  text-decoration-color: #cbd5e1;
}
.rule-drawer__event--suppressed > td:last-child {
  /* Keep the action button readable — only the data columns strike. */
  text-decoration: none;
}

.rule-drawer__suppress-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 2px 6px;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
}
.rule-drawer__suppress-btn:hover:not(:disabled) {
  background: #f1f5f9;
  border-color: #cbd5e1;
}
.rule-drawer__suppress-btn:disabled {
  opacity: 0.4;
  cursor: wait;
}
</style>
