<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import api from '../api'
import Badge from './Badge.vue'
import Button from './ui/Button.vue'
import Checkbox from './ui/Checkbox.vue'
import RadioGroup from './ui/RadioGroup.vue'
import { useFlash } from '../composables/useFlash'

// Extracted from PatternsView. Owns the batch-import-from-folder modal: its
// path/conflict inputs, scan + import calls, and the candidate/result tables.
// All of this state is local to the modal, so it lives here rather than in the
// parent — keeping the markup and its directives off PatternsView's budget.
// The parent drives open/close via v-model:visible and reloads the list on
// `imported`. The component tag stays mounted in the parent, so folderPath /
// folderOnConflict persist across opens just like the old module-level refs.
const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'imported'])

const { flash } = useFlash()

const defaultFolderPath = ref('~/.claude/skills')
const folderPath = ref('~/.claude/skills')
const folderOnConflict = ref('skip')
const folderScanning = ref(false)
const folderImporting = ref(false)
const folderCandidates = ref([])     // [{name, derived_slug, conflict, error}]
const selectedNames = ref(new Set()) // candidate names the user chose to import
const folderResolvedPath = ref('')   // absolute path the server resolved
const folderResults = ref([])        // [{name, status, slug, ...}] after import
const folderResultPath = ref('')
const folderCounts = ref({})

onMounted(async () => {
  try {
    const providers = await api.get('/providers')
    const paths = providers?.skill_paths
    if (paths?.global_dir) {
      defaultFolderPath.value = paths.global_dir
      folderPath.value = paths.global_dir
    }
  } catch {
    // keep Claude default
  }
})

// Partial reset on each open — mirrors the old openFolderImport: clears the
// scan/result state but deliberately preserves folderPath + folderOnConflict.
watch(() => props.visible, (on) => {
  if (!on) return
  folderCandidates.value = []
  selectedNames.value = new Set()
  folderResults.value = []
  folderCounts.value = {}
  folderResolvedPath.value = ''
  folderResultPath.value = ''
  // If the user never edited the path, keep it synced with the active provider.
  if (folderPath.value === '~/.claude/skills' || folderPath.value === defaultFolderPath.value) {
    folderPath.value = defaultFolderPath.value
  }
})

function closeFolderImport() {
  if (folderScanning.value || folderImporting.value) return
  emit('update:visible', false)
}

async function scanFolder() {
  const path = folderPath.value.trim()
  if (!path) { flash('Enter a folder path', 'error'); return }
  folderScanning.value = true
  folderResults.value = []
  try {
    const payload = await api.post('/patterns/import-dir/scan', { path })
    if (!payload.ok) {
      flash(payload.msg || 'Scan failed', 'error')
      folderCandidates.value = []
      folderResolvedPath.value = ''
      return
    }
    folderCandidates.value = payload.candidates || []
    // Default: pre-select every importable (non-error) candidate — opt-out.
    selectedNames.value = new Set(importableNames.value)
    folderResolvedPath.value = payload.path || ''
    if (!folderCandidates.value.length) {
      flash(`No <name>/SKILL.md found under ${payload.path}`, 'warn')
    }
  } finally {
    folderScanning.value = false
  }
}

async function runFolderImport() {
  if (!folderCandidates.value.length) { flash('Scan first', 'error'); return }
  folderImporting.value = true
  try {
    const payload = await api.post('/patterns/import-dir', {
      path: folderResolvedPath.value || folderPath.value.trim(),
      on_conflict: folderOnConflict.value,
      selected: [...selectedNames.value],
    })
    if (!payload.ok) { flash(payload.msg || 'Import failed', 'error'); return }
    folderResults.value = payload.results || []
    folderCounts.value = payload.counts || {}
    folderResultPath.value = payload.path || ''
    const c = payload.counts || {}
    const imported = (c.imported || 0) + (c.overwritten || 0) + (c.renamed || 0)
    const gritTotal = (payload.results || []).reduce((n, r) => n + ((r.grit_rules || []).length), 0)
    const gritNote = gritTotal ? ` (+${gritTotal} grit rule(s))` : ''
    flash(`Imported ${imported} skill(s) from ${payload.path}${gritNote}`, imported ? 'success' : 'warn')
    emit('imported')
  } finally {
    folderImporting.value = false
  }
}

// Names that can ever be imported (parsed a slug, no scan error).
const importableNames = computed(() =>
  folderCandidates.value.filter((c) => !c.error).map((c) => c.name))

// Selected candidates that would actually import under the current policy
// ('skip' won't touch conflicts, so they don't count toward the button label).
const folderImportableCount = computed(() =>
  folderCandidates.value.filter((c) =>
    selectedNames.value.has(c.name) && !c.error
    && (folderOnConflict.value !== 'skip' || !c.conflict),
  ).length)

const allImportableSelected = computed(() =>
  importableNames.value.length > 0
  && importableNames.value.every((n) => selectedNames.value.has(n)))

function toggleCandidate(name, checked) {
  const next = new Set(selectedNames.value)
  if (checked) next.add(name); else next.delete(name)
  selectedNames.value = next
}

function toggleAll(checked) {
  const next = new Set(selectedNames.value)
  for (const n of importableNames.value) {
    if (checked) next.add(n); else next.delete(n)
  }
  selectedNames.value = next
}

const folderResultGlyph = {
  imported: '+',
  overwritten: '↻',
  renamed: '~',
  skipped: '·',
  failed: '!',
  planned: '·',
}
</script>

<template>
  <Teleport to="body">
    <aside
      v-if="visible"
      class="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="folder-import-title"
      @click.self="closeFolderImport"
      @keydown.esc="closeFolderImport"
    >
      <div class="modal-card modal-card-wide">
        <div class="modal-body">
          <h2 id="folder-import-title" class="modal-title">Batch import skills from folder</h2>
          <p class="modal-text">
            Walks each <code>&lt;folder&gt;/&lt;name&gt;/SKILL.md</code> and imports it as a pattern.
            Path is resolved on the regin server (this machine).
          </p>

          <div class="flex gap-2 mt-3">
            <input
              v-model="folderPath"
              type="text"
              aria-label="Folder path to scan"
              :placeholder="defaultFolderPath"
              class="input font-mono flex-1 focus-visible:outline-2 focus-visible:outline-blue-500"
              @keydown.enter.prevent="scanFolder"
            />
            <Button variant="secondary"
                    :disabled="folderScanning || folderImporting"
                    @click="scanFolder">
              {{ folderScanning ? 'Scanning…' : 'Scan' }}
            </Button>
          </div>
          <p v-if="folderResolvedPath" class="text-[11px] text-slate-500 mt-1 font-mono">
            resolved → {{ folderResolvedPath }}
          </p>

          <div v-if="folderCandidates.length" class="mt-4 max-h-72 overflow-y-auto border border-slate-200 rounded">
            <table class="tbl tbl-compact">
              <thead>
                <tr>
                  <th class="w-8 text-center">
                    <Checkbox
                      :model-value="allImportableSelected"
                      aria-label="Select all importable skills"
                      @update:model-value="toggleAll" />
                  </th>
                  <th>Folder</th><th>Derived slug</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in folderCandidates" :key="c.name">
                  <td class="text-center">
                    <Checkbox
                      :model-value="selectedNames.has(c.name)"
                      :disabled="!!c.error"
                      :aria-label="`Import ${c.name}`"
                      @update:model-value="(v) => toggleCandidate(c.name, v)" />
                  </td>
                  <td class="font-mono text-[12px]">{{ c.name }}</td>
                  <td class="font-mono text-[12px]">{{ c.derived_slug || '—' }}</td>
                  <td>
                    <Badge v-if="c.error" color="red" :label="c.error" />
                    <Badge v-else-if="c.conflict" color="yellow" label="already exists" />
                    <Badge v-else color="green" label="new" />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="folderCandidates.length" class="mt-4">
            <label class="block text-sm font-medium text-slate-700 mb-1">On conflict:</label>
            <RadioGroup
              v-model="folderOnConflict"
              inline
              :options="[
                { value: 'skip', label: 'Skip' },
                { value: 'overwrite', label: 'Overwrite' },
                { value: 'rename', label: 'Rename (-2, -3, …)' },
              ]" />
          </div>

          <div v-if="folderResults.length" class="mt-4 max-h-72 overflow-y-auto border border-slate-200 rounded">
            <table class="tbl tbl-compact">
              <thead>
                <tr><th></th><th>Folder</th><th>Slug</th><th>Status</th><th>Note</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in folderResults" :key="r.name">
                  <td class="font-mono text-[14px] text-slate-500 text-center">
                    {{ folderResultGlyph[r.status] || '?' }}
                  </td>
                  <td class="font-mono text-[12px]">{{ r.name }}</td>
                  <td class="font-mono text-[12px]">{{ r.slug || '—' }}</td>
                  <td>
                    <Badge
                      :color="r.status === 'imported' ? 'green'
                            : r.status === 'overwritten' ? 'blue'
                            : r.status === 'renamed' ? 'purple'
                            : r.status === 'skipped' ? 'gray'
                            : r.status === 'failed' ? 'red' : 'gray'"
                      :label="r.status"
                    />
                  </td>
                  <td class="text-[12px] text-slate-600">
                    {{ r.error || (r.file_count != null ? `${r.file_count} file(s)` : '') }}
                    <span v-if="r.grit_rules && r.grit_rules.length" class="text-emerald-600">
                      +{{ r.grit_rules.length }} grit rule(s)
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="modal-footer">
          <Button variant="secondary"
                  :disabled="folderScanning || folderImporting"
                  @click="closeFolderImport">
            {{ folderResults.length ? 'Close' : 'Cancel' }}
          </Button>
          <Button v-if="folderCandidates.length && !folderResults.length"
                  variant="primary"
                  :disabled="folderImporting || folderImportableCount === 0"
                  @click="runFolderImport">
            {{ folderImporting ? 'Importing…'
               : `Import ${folderImportableCount} skill${folderImportableCount === 1 ? '' : 's'}` }}
          </Button>
        </div>
      </div>
    </aside>
  </Teleport>
</template>

<style scoped>
.modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.4);
    backdrop-filter: blur(4px);
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.modal-card {
    background: var(--color-white);
    border-radius: 1rem;
    max-width: 28rem;
    width: 100%;
    box-shadow: 0 24px 64px rgba(15, 23, 42, 0.25);
    overflow: hidden;
}

.modal-card-wide {
    max-width: 48rem;
}

.tbl-compact th,
.tbl-compact td {
    padding: 0.4rem 0.6rem;
    font-size: 0.8125rem;
}

.modal-body { padding: 1.25rem 1.25rem 1rem; }

.modal-title {
    font-size: 1rem;
    font-weight: 600;
    color: var(--color-slate-900);
    margin-bottom: 0.375rem;
}

.modal-text { font-size: 0.875rem; color: var(--color-slate-500); }

.modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    padding: 0.75rem 1.25rem 1rem;
    border-top: 1px solid var(--color-slate-100);
}
</style>
