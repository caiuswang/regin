<script setup>
/**
 * HistoryPanel — snapshot history with restore/pin/unpin.
 *
 * Calls /api/repos/<name>/topics/snapshots; renders newest-first with
 * the diff_summary inline. Restore creates a NEW is_latest snapshot
 * that mirrors the chosen older one; pin protects a row from prune.
 */
import { onMounted, ref } from 'vue'
import api from '../../api'

const props = defineProps({
  repoName: { type: String, required: true },
})

const emit = defineEmits(['restored'])

const snapshots = ref([])
const loading = ref(false)
const acting = ref(null)  // snapshot_id currently being acted on, for spinner state
const error = ref('')

// Restore-preview modal state. Click "Restore" first loads a preview;
// the user confirms inside the modal before any mutation happens.
const previewSnap = ref(null)      // snapshot row currently being previewed
const previewData = ref(null)      // /restore-preview response payload
const previewLoading = ref(false)
const previewError = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get(`/repos/${props.repoName}/topics/snapshots`)
    if (res?.ok) {
      snapshots.value = res.snapshots || []
    } else {
      error.value = res?.error || 'Failed to load snapshots'
    }
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

async function openPreview(snap) {
  if (snap.is_latest) return
  previewSnap.value = snap
  previewData.value = null
  previewError.value = ''
  previewLoading.value = true
  try {
    const res = await api.get(`/repos/${props.repoName}/topics/snapshots/${snap.id}/restore-preview`)
    if (res?.ok) {
      previewData.value = res.preview
    } else {
      previewError.value = res?.error || 'Failed to load preview'
    }
  } catch (err) {
    previewError.value = err?.message || String(err)
  } finally {
    previewLoading.value = false
  }
}

function closePreview() {
  previewSnap.value = null
  previewData.value = null
  previewError.value = ''
}

async function confirmRestore() {
  const snap = previewSnap.value
  if (!snap) return
  acting.value = snap.id
  error.value = ''
  try {
    const res = await api.post(`/repos/${props.repoName}/topics/snapshots/${snap.id}/restore`, {})
    if (res?.ok) {
      closePreview()
      await refresh()
      emit('restored', { snapshotId: res.snapshot?.id })
    } else {
      previewError.value = res?.error || 'Restore failed'
    }
  } catch (err) {
    previewError.value = err?.message || String(err)
  } finally {
    acting.value = null
  }
}

async function togglePin(snap) {
  acting.value = snap.id
  error.value = ''
  const op = snap.pinned ? 'unpin' : 'pin'
  try {
    const res = await api.post(`/repos/${props.repoName}/topics/snapshots/${snap.id}/${op}`, {})
    if (res?.ok) {
      await refresh()
    } else {
      error.value = res?.error || `${op} failed`
    }
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    acting.value = null
  }
}

function summaryLine(snap) {
  const s = snap.summary || {}
  const bits = []
  if (s.strategy) bits.push(s.strategy)
  if (s.proposed_topic_id) bits.push(s.proposed_topic_id)
  if (s.alias_adds) bits.push(`+${s.alias_adds} aliases`)
  if (s.ref_adds) bits.push(`+${s.ref_adds} refs`)
  if (s.edge_adds) bits.push(`+${s.edge_adds} edges`)
  return bits.length ? bits.join(' · ') : '(no summary)'
}

onMounted(refresh)
</script>

<template>
  <section class="space-y-3" data-testid="history-panel">
    <header class="flex items-center justify-between">
      <div>
        <h3 class="text-base font-semibold text-slate-900">Graph history</h3>
        <p class="text-xs text-slate-500">
          Newest first. Restore creates a new snapshot mirroring the chosen one.
        </p>
      </div>
      <button
        type="button"
        class="btn btn-secondary text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? 'Refreshing…' : 'Refresh' }}
      </button>
    </header>

    <p v-if="error" class="text-sm text-red-700">{{ error }}</p>
    <p v-if="!loading && !snapshots.length && !error" class="text-sm text-slate-500 italic">
      No snapshots yet — apply a proposal to create the first.
    </p>

    <ul class="space-y-2">
      <li
        v-for="snap in snapshots"
        :key="snap.id"
        :class="['border rounded p-3 flex items-start justify-between gap-3',
                 snap.is_latest ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-white']"
        data-testid="history-snapshot"
      >
        <div class="space-y-0.5 text-xs">
          <div class="font-mono">
            #{{ snap.id }}
            <span v-if="snap.is_latest" class="text-blue-700 font-semibold">latest</span>
            <span v-if="snap.pinned" class="text-amber-700 ml-2">pinned</span>
          </div>
          <div class="text-slate-500">
            <span class="font-semibold text-slate-700">{{ snap.reason }}</span> · {{ snap.taken_at }}
          </div>
          <div class="text-slate-700">{{ summaryLine(snap) }}</div>
          <div v-if="snap.triggering_run_id" class="text-slate-400">
            from run <code>{{ snap.triggering_run_id }}</code>
          </div>
        </div>
        <div class="btn-row flex gap-2">
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="acting === snap.id"
            @click="togglePin(snap)"
          >
            {{ snap.pinned ? 'Unpin' : 'Pin' }}
          </button>
          <button
            type="button"
            class="btn btn-primary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="acting === snap.id || snap.is_latest"
            @click="openPreview(snap)"
          >
            {{ acting === snap.id ? '…' : (snap.is_latest ? 'Live' : 'Restore…') }}
          </button>
        </div>
      </li>
    </ul>

    <!-- Restore preview modal -->
    <div
      v-if="previewSnap"
      class="fixed inset-0 z-40 flex items-start justify-center bg-slate-900/40 p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="restore-preview-title"
    >
      <div class="bg-white rounded shadow-lg w-full max-w-2xl mt-12 border border-slate-200">
        <header class="px-4 py-3 border-b border-slate-200 flex items-start justify-between gap-3">
          <div>
            <h4 id="restore-preview-title" class="text-sm font-semibold text-slate-900">
              Restore snapshot #{{ previewSnap.id }}
            </h4>
            <p class="text-xs text-slate-500 mt-0.5">
              {{ previewSnap.reason }} · {{ previewSnap.taken_at }}
            </p>
          </div>
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            aria-label="Close preview"
            @click="closePreview"
          >
            Close
          </button>
        </header>

        <div class="px-4 py-3 space-y-3 text-xs">
          <p v-if="previewLoading" class="text-slate-500 italic">Loading preview…</p>
          <p v-if="previewError" class="text-red-700">{{ previewError }}</p>

          <template v-if="previewData">
            <p v-if="previewData.no_change" class="text-slate-600">
              Restoring would not change the graph — this snapshot already matches the current state.
            </p>

            <ul v-else class="space-y-2">
              <li
                v-for="d in previewData.topic_deltas"
                :key="d.topic_id + ':' + d.kind"
                class="border border-slate-200 rounded p-2 bg-slate-50"
              >
                <div class="flex items-center justify-between">
                  <code class="font-mono text-slate-800">{{ d.topic_id }}</code>
                  <span
                    :class="['text-xs px-1.5 py-0.5 rounded border',
                             d.kind === 'would_remove' ? 'border-red-300 text-red-700 bg-red-50'
                               : d.kind === 'would_add_back' ? 'border-emerald-300 text-emerald-700 bg-emerald-50'
                               : 'border-amber-300 text-amber-700 bg-amber-50']"
                  >
                    {{ d.kind === 'would_remove' ? 'will be removed'
                       : d.kind === 'would_add_back' ? 'will be added back'
                       : 'will revert' }}
                  </span>
                </div>
                <div v-if="d.alias_adds.length || d.alias_removes.length" class="text-slate-600 mt-1">
                  aliases:
                  <span v-if="d.alias_adds.length" class="text-emerald-700">+{{ d.alias_adds.join(', ') }}</span>
                  <span v-if="d.alias_adds.length && d.alias_removes.length"> · </span>
                  <span v-if="d.alias_removes.length" class="text-red-700">−{{ d.alias_removes.join(', ') }}</span>
                </div>
                <div v-if="d.ref_adds.length || d.ref_removes.length" class="text-slate-600 mt-1">
                  refs: +{{ d.ref_adds.length }} / −{{ d.ref_removes.length }}
                </div>
                <div v-if="d.edge_adds.length || d.edge_removes.length" class="text-slate-600 mt-1">
                  edges: +{{ d.edge_adds.length }} / −{{ d.edge_removes.length }}
                </div>
                <div v-if="d.scalar_changes.length" class="text-slate-600 mt-1">
                  <span v-for="[field, before, after] in d.scalar_changes" :key="field" class="block">
                    {{ field }}: <span class="text-red-700">{{ before ?? '∅' }}</span>
                    →
                    <span class="text-emerald-700">{{ after ?? '∅' }}</span>
                  </span>
                </div>
              </li>
            </ul>

            <p v-if="previewData.wiki_changes && previewData.wiki_changes.length" class="text-slate-600">
              Wiki bodies also differ for:
              <code class="font-mono">{{ previewData.wiki_changes.join(', ') }}</code>
            </p>
          </template>
        </div>

        <footer class="px-4 py-3 border-t border-slate-200 flex justify-end gap-2">
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            @click="closePreview"
          >
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-primary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="previewLoading || acting === previewSnap.id"
            @click="confirmRestore"
          >
            {{ acting === previewSnap.id ? 'Restoring…' : 'Confirm restore' }}
          </button>
        </footer>
      </div>
    </div>
  </section>
</template>
