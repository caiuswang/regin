<script setup>
import { computed } from 'vue'
import DiffBlock from '../../DiffBlock.vue'
import { fmtBytes, diffOpLabel } from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

// The fourth section of a Bash card: what the command did to the files it
// touched. Its own child because BashCard already carries three sections and
// a fourth inline breaches the vue-complexity template metrics.
const props = defineProps({
  attrs: { type: Object, required: true },
  // The three sections above this one are optional — a silent `sed -i` has
  // none of them — so the separator is the parent's call, not ours.
  divided: { type: Boolean, default: false },
})

const edits = computed(() => props.attrs.bash_edits || [])

// Paths Claude Code named as changed but shipped no hunks for — the capped
// overflow. Listed by name so the reader still learns which files moved.
const extraPaths = computed(() => {
  const shown = new Set(edits.value.map(e => e.file_path))
  return (props.attrs.bash_edit_files || []).filter(p => !shown.has(p))
})

const moreCount = computed(() => props.attrs.bash_edit_more || 0)
</script>

<template>
  <div class="px-3 py-2" :class="divided ? 'border-t border-slate-800' : ''">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-orange-400 mb-1.5">file changes</div>
    <div v-for="(e, i) in edits" :key="e.file_path + i" class="mb-2 last:mb-0" data-bash-edit>
      <div class="flex items-center gap-2 mb-1">
        <span class="font-mono text-[11px] text-slate-300">
          <span class="font-semibold">{{ diffOpLabel(e.edit_op) }}</span><span class="text-slate-500">({{ e.file_path }})</span>
        </span>
        <span v-if="e.added_lines" class="font-mono text-[10.5px] text-emerald-300">+{{ e.added_lines }}</span>
        <span v-if="e.removed_lines" class="font-mono text-[10.5px] text-red-300">−{{ e.removed_lines }}</span>
        <span
          v-if="e.diff_truncated_bytes"
          class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
        >diff too large ({{ fmtBytes(e.diff_truncated_bytes) }})</span>
        <CopyButton v-if="e.diff" :text="e.diff" />
      </div>
      <DiffBlock v-if="e.diff" :diff="e.diff" :file-path="e.file_path" />
      <div v-else-if="!e.diff_truncated_bytes" class="font-mono text-[11px] text-slate-500">no diff supplied</div>
    </div>
    <div v-if="attrs.bash_edit_unavailable" class="font-mono text-[11px] text-slate-400 mt-1">diff unavailable</div>
    <div v-if="extraPaths.length" class="font-mono text-[11px] text-slate-400 mt-1">
      <div v-for="p in extraPaths" :key="p" data-bash-edit-path>{{ p }}</div>
    </div>
    <div v-else-if="moreCount" class="font-mono text-[11px] text-slate-400 mt-1">+{{ moreCount }} more files</div>
  </div>
</template>
