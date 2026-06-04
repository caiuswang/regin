<script setup>
import Badge from './Badge.vue'
import DiffBlock from './DiffBlock.vue'
import DriftDetailGrid from './DriftDetailGrid.vue'

defineProps({
  s: { type: Object, required: true },
  activeTab: { type: String, required: true },
  schemaDoc: { type: Object, default: null },
  diff: { type: Object, default: null },
  drifts: { type: Array, default: null },
  detailById: { type: Object, required: true },
  detailLoading: { type: Object, required: true },
  expandedDrift: { type: [Number, String], default: null },
})

defineEmits(['set-tab', 'toggle-drift', 'ratify', 'ignore', 'discard'])

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

function fmtSample(s) {
  if (!s) return ''
  return s.length > 80 ? s.slice(0, 80) + '…' : s
}

function pretty(obj) {
  if (obj === null || obj === undefined) return ''
  return JSON.stringify(obj, null, 2)
}
</script>

<template>
  <div class="expansion">
    <div class="segmented">
      <button
        type="button"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': activeTab === 'schema' }"
        @click.stop="$emit('set-tab', 'schema')"
      >Schema</button>
      <button
        v-if="s.pending > 0"
        type="button"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': activeTab === 'diff' }"
        @click.stop="$emit('set-tab', 'diff')"
      >
        Diff
        <span class="seg-count">{{ s.pending }}</span>
      </button>
      <button
        type="button"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': activeTab === 'findings' }"
        @click.stop="$emit('set-tab', 'findings')"
      >
        Findings
        <span v-if="s.pending" class="seg-count">{{ s.pending }}</span>
      </button>
    </div>

    <!-- Schema tab -->
    <div v-if="activeTab === 'schema'" class="tab-panel">
      <div v-if="!schemaDoc" class="empty-state-inline">Loading schema…</div>
      <div v-else>
        <dl class="meta-kv">
          <dt>Baseline</dt>
          <dd><code class="cell-code">{{ schemaDoc.baseline_path }}</code></dd>
          <dt>Overlay</dt>
          <dd class="meta-kv__overlay">
            <code class="cell-code">{{ schemaDoc.overlay_path }}</code>
            <Badge
              v-if="!schemaDoc.overlay_exists"
              color="gray" label="not yet"
            />
            <Badge v-else color="blue" label="overlay active" />
          </dd>
        </dl>
        <pre class="code-block">{{ pretty(schemaDoc.schema) }}</pre>
      </div>
    </div>

    <!-- Diff tab -->
    <div v-else-if="activeTab === 'diff'" class="tab-panel">
      <div v-if="!diff" class="empty-state-inline">Loading diff…</div>
      <div v-else>
        <p class="tab-help">
          Preview the overlay after ratifying every pending unknown_field for this schema
          ({{ diff.pending_count }} change<span v-if="diff.pending_count !== 1">s</span>).
        </p>
        <div v-if="diff.unified_diff" class="diff-wrap">
          <DiffBlock
            :diff="diff.unified_diff"
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
      <div v-if="!drifts" class="empty-state-inline pad">
        Loading findings…
      </div>
      <div v-else-if="!drifts.length" class="empty-state-inline pad">
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
          <template v-for="r in drifts" :key="r.id">
            <tr
              class="row-clickable focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="{ 'tbl-row-active': expandedDrift === r.id }"
              tabindex="0"
              @click.stop="$emit('toggle-drift', r)"
              @keydown.enter.prevent.stop="$emit('toggle-drift', r)"
              @keydown.space.prevent.stop="$emit('toggle-drift', r)"
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
                  @click.stop="$emit('ratify', r)"
                >Ratify</button>
                <button
                  type="button"
                  class="btn btn-ghost focus-visible:outline-2 focus-visible:outline-blue-500"
                  @click.stop="$emit('ignore', r)"
                >Ignore</button>
                <button
                  type="button"
                  class="btn btn-ghost btn-danger focus-visible:outline-2 focus-visible:outline-blue-500"
                  @click.stop="$emit('discard', r)"
                >Discard</button>
              </td>
            </tr>

            <tr v-if="expandedDrift === r.id" class="expansion-row expansion-row--inner">
              <td colspan="7">
                <div v-if="detailLoading[r.id]" class="empty-state-inline pad">
                  Loading detail…
                </div>
                <DriftDetailGrid v-else-if="detailById[r.id]" :detail="detailById[r.id]" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.tab-panel { display: flex; flex-direction: column; gap: 0.625rem; }
.tab-panel--flush { padding: 0; gap: 0; }
.tab-help { font-size: 0.8125rem; color: #475569; line-height: 1.5; margin: 0; }
.diff-wrap {
  background: #0F172A;
  border-radius: 0.5rem;
  overflow: hidden;
}

.expansion {
  padding: 0.875rem 1.125rem;
  display: flex; flex-direction: column; gap: 0.875rem;
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

/* Tables — column widths so expanded JSON can't reflow header cols. */
.findings-tbl { table-layout: fixed; }
.caret-col { width: 1.5rem; }
.actions-col { text-align: right; white-space: nowrap; width: 14rem; }

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

.sample { color: #475569; word-break: break-all; }

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
/* Base .expansion-row td styling lived in the parent's scoped block, but the
   inner .expansion-row element moved into this child — re-scope here so it
   isn't stranded (padding:0!important + border-bottom; background is left to
   the --inner rule above). */
.expansion-row > td { padding: 0 !important; border-bottom: 1px solid #E2E8F0; }
.expansion-row:hover { background: transparent; }

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

/* Code blocks — dark only because they show code/JSON. */
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
