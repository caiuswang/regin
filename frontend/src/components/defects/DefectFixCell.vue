<script setup>
// A defect's fix task, read where the row is: the chip carries the task's
// live title, and its status dot is the list's own `fix_state` (the
// `?with_fix_state=1` read), so a list re-read moves it without a second
// resolve. A pointer whose task no longer exists has no `fix_state` — that
// is "gone", not "pending".
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import RefChip from '../refs/RefChip.vue'
import { followChip } from '../../utils/refRender.js'

const props = defineProps({
  defect: { type: Object, required: true },
  // The resolved `RefItem` for `task:<fix_task_id>`; undefined while in flight.
  item: { type: Object, default: undefined },
})

const router = useRouter()

const tone = computed(() => {
  const d = props.defect
  if (!d.fix_task_id) return d.status === 'open' ? 'none' : 'empty'
  if (d.fix_state == null) return 'gone'
  return d.status === 'open' ? 'warn' : 'neutral'
})

// Until the resolve lands (or if it fails) the chip is still the way through
// to the task: its key and the list's own status, without the title.
const chipItem = computed(() => {
  const id = props.defect.fix_task_id
  const base = props.item || { kind: 'task', key: String(id), title: '', url: `/tasks/${id}` }
  return { ...base, status: props.defect.fix_state }
})
</script>

<template>
  <span class="dfc" :data-tone="tone" data-pl-part="fix-cell"
        :data-testid="`defect-fix-${defect.id}`" @click="e => followChip(e, router.push)">
    <template v-if="tone === 'warn' || tone === 'neutral'">
      <RefChip :target="`task:${defect.fix_task_id}`" :item="chipItem" />
      <span v-if="defect.fix_drift" class="ledger-tag ledger-tag-amber"
            data-pl-part="tag" data-tone="warn"
            :data-testid="`defect-fix-drift-${defect.id}`">fix done · still open</span>
    </template>
    <span v-else-if="tone === 'none'" class="dfc-note">no fix task</span>
    <span v-else-if="tone === 'gone'" class="dfc-note"
          :title="`task #${defect.fix_task_id} no longer exists`">fix task gone</span>
  </span>
</template>

<style scoped>
.dfc { display: flex; align-items: center; gap: 0.3rem; min-width: 0; overflow: hidden; }
.dfc :deep(.ref-chip) { flex: 0 1 auto; min-width: 0; font-size: 11.5px; }
.dfc[data-tone="warn"] :deep(.ref-chip) {
    border-color: var(--color-warning);
    background: var(--color-warning-soft);
}
.dfc-note { font-size: 11px; color: var(--color-fg-faint); white-space: nowrap; }
</style>
