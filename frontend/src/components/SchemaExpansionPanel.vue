<script setup>
import { computed } from 'vue'
import Badge from './Badge.vue'
import DiffBlock from './DiffBlock.vue'
import DriftDetailGrid from './DriftDetailGrid.vue'
import Button from './ui/Button.vue'
import Tabs from './ui/Tabs.vue'
import { driftKindColor } from '../composables/useBadgeColor'

const props = defineProps({
  s: { type: Object, required: true },
  activeTab: { type: String, required: true },
  schemaDoc: { type: Object, default: null },
  diff: { type: Object, default: null },
  drifts: { type: Array, default: null },
  detailById: { type: Object, required: true },
  detailLoading: { type: Object, required: true },
  expandedDrift: { type: [Number, String], default: null },
})

const emit = defineEmits(['set-tab', 'toggle-drift', 'ratify', 'ignore', 'discard'])

// Tab definitions. The Diff tab is conditional on pending drift (the
// original `v-if="s.pending > 0"`); Schema and Findings are always shown.
// Counts ride along as a label suffix so the segmented control keeps the
// pending badge the old `.seg-count` pill carried.
const tabs = computed(() => {
  const list = [{ value: 'schema', label: 'Schema' }]
  if (props.s.pending > 0) list.push({ value: 'diff', label: `Diff ${props.s.pending}` })
  list.push({ value: 'findings', label: props.s.pending ? `Findings ${props.s.pending}` : 'Findings' })
  return list
})

// Tabs v-model emits `set-tab` upward so the parent keeps owning activeTab.
const activeTabModel = computed({
  get: () => props.activeTab,
  set: (v) => emit('set-tab', v),
})

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
    <Tabs v-model="activeTabModel" :tabs="tabs" variant="segmented" />

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
      <div v-else class="overflow-x-auto">
      <table class="tbl findings-tbl">
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
                  :color="driftKindColor(r.drift_kind)"
                  :label="KIND_LABEL[r.drift_kind] || r.drift_kind"
                />
              </td>
              <td><code class="cell-code sample">{{ fmtSample(r.sample_value) }}</code></td>
              <td><code class="cell-code">{{ r.claude_version || '—' }}</code></td>
              <td class="text-right tabular">{{ r.occurrence_count }}</td>
              <td class="actions-col">
                <Button
                  v-if="r.drift_kind === 'unknown_field'"
                  variant="primary"
                  size="sm"
                  class="ml-1"
                  title="Add this field to your local overlay"
                  @click.stop="$emit('ratify', r)"
                >Ratify</Button>
                <Button
                  variant="ghost"
                  size="sm"
                  class="ml-1"
                  @click.stop="$emit('ignore', r)"
                >Ignore</Button>
                <Button
                  variant="danger"
                  size="sm"
                  class="ml-1"
                  @click.stop="$emit('discard', r)"
                >Discard</Button>
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
  </div>
</template>

<style scoped>
.tab-panel { display: flex; flex-direction: column; gap: 0.625rem; }
.tab-panel--flush { padding: 0; gap: 0; }
.tab-help { font-size: 0.8125rem; color: var(--color-slate-600); line-height: 1.5; margin: 0; }
.diff-wrap {
  background: var(--code-bg);
  border-radius: 0.5rem;
  overflow: hidden;
}

.expansion {
  padding: 0.875rem 1.125rem;
  display: flex; flex-direction: column; gap: 0.875rem;
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
  color: var(--color-slate-400); font-size: 0.75rem;
  transition: transform 120ms ease;
  display: inline-block;
}
.caret.open { transform: rotate(90deg); color: var(--color-blue-800); }

.tabular { font-variant-numeric: tabular-nums; }
.text-right { text-align: right; }

.sample { color: var(--color-slate-600); word-break: break-all; }

/* Findings table (nested inside the expansion) -------------------- */
.findings-tbl {
  background: var(--color-white);
  border: 1px solid var(--color-slate-200);
  border-radius: 0.625rem;
  font-size: 0.8125rem;
}
.findings-tbl thead { background: var(--color-slate-50); }
.expansion-row--inner > td {
  background: var(--color-slate-50);
  border-top: 1px solid var(--color-slate-200);
}
/* Base .expansion-row td styling lived in the parent's scoped block, but the
   inner .expansion-row element moved into this child — re-scope here so it
   isn't stranded (padding:0!important + border-bottom; background is left to
   the --inner rule above). */
.expansion-row > td { padding: 0 !important; border-bottom: 1px solid var(--color-slate-200); }
.expansion-row:hover { background: transparent; }

/* Schema/detail key-value rows ------------------------------------ */
.meta-kv {
  display: grid;
  grid-template-columns: 6rem 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
  font-size: 0.8125rem;
}
.meta-kv dt { color: var(--color-slate-400); font-weight: 500; }
.meta-kv dd { margin: 0; color: var(--color-slate-800); min-width: 0; }
.meta-kv__overlay { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }

/* Code blocks — dark only because they show code/JSON. */
.code-block {
  background: var(--code-bg); color: var(--code-fg);
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
  color: var(--color-slate-500);
  margin: 0;
}
.empty-state-inline.pad { padding: 0.75rem 0.25rem; }
</style>
