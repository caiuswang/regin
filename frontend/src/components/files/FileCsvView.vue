<script setup>
// CSV / TSV as a table. Papa Parse does the splitting (quoted fields,
// embedded newlines) and detects the delimiter itself, so nothing here looks
// at the extension again (I7). The first row is the header; only the first
// ROW_CAP data rows are drawn, and the notice says how many were left out.
import Papa from 'papaparse'
import { computed, onMounted, ref, shallowRef } from 'vue'
import api from '../../api'
import { rawUrl } from './source'

const ROW_CAP = 1000

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['failed'])

const rows = shallowRef(null)
const error = ref('')

const header = computed(() => rows.value?.[0] || [])
const total = computed(() => Math.max((rows.value?.length || 0) - 1, 0))
const shown = computed(() => (rows.value ? rows.value.slice(1, ROW_CAP + 1) : []))
const truncated = computed(() => total.value > ROW_CAP)
const fmt = (n) => n.toLocaleString('en-US')

onMounted(async () => {
  try {
    const text = await api.getText(rawUrl(props.source))
    rows.value = Papa.parse(text, { skipEmptyLines: true }).data
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
  }
})
</script>

<template>
  <div class="fcsv" data-testid="file-csv-view">
    <p v-if="error" class="fcsv-note fcsv-error" role="alert">{{ error }}</p>
    <p v-else-if="!rows" class="fcsv-note" role="status">Loading…</p>
    <template v-else>
      <p v-if="truncated" class="fcsv-notice" role="status" data-testid="file-csv-truncated">
        Showing the first {{ fmt(ROW_CAP) }} of {{ fmt(total) }} rows.
      </p>
      <div class="fcsv-scroll">
        <table class="fcsv-table">
          <thead>
            <tr>
              <th v-for="(cell, i) in header" :key="i" scope="col">{{ cell }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, r) in shown" :key="r">
              <td v-for="(cell, i) in row" :key="i">{{ cell }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.fcsv {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.fcsv-note { padding: 1rem 1.25rem; font-size: 13px; color: var(--color-fg-muted); }
.fcsv-error { color: var(--color-danger); }
.fcsv-notice {
  padding: 0.4rem 0.85rem;
  font-size: 12px;
  color: var(--color-fg-muted);
  border-bottom: 1px solid var(--color-border);
}
.fcsv-scroll { flex: 1; min-height: 0; overflow: auto; }
.fcsv-table {
  border-collapse: collapse;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}
.fcsv-table th,
.fcsv-table td {
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--color-border);
  white-space: nowrap;
  text-align: left;
}
.fcsv-table th {
  position: sticky;
  top: 0;
  background: var(--color-surface-2);
  font-weight: 600;
}
</style>
