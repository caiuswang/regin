<script setup>
// Naming a task as a run of another — the one act the grain has no other
// surface for.
//
// The options are the server's answer to "who may be this task's parent",
// asked of the same function that refuses the write, so the picker never
// offers an act the PATCH would reject. The search box narrows by re-asking
// the server; it is a way into a long backlog, never a rule about what is
// legal.
import { onMounted, onUnmounted, ref, watch } from 'vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { candidateLabel, useGrainCandidates }
  from '../../composables/useGrainCandidates.js'

const props = defineProps({
  // The task being edited, or 0 for one the composer has not captured yet —
  // the id `capture_task` itself checks the parentage under.
  child: { type: Number, default: 0 },
  // Set instead of `child` to stand on the other side of the relationship:
  // who may become a run OF this task. Same rule, arguments swapped.
  parent: { type: Number, default: null },
  label: { type: String, default: 'Parent feature' },
  hint: { type: String, default: 'makes this task a run of it' },
  emptyLabel: { type: String, default: 'No parent (top-level)' },
})
// '' is top-level; a string id is a declared parentage, because a Select
// round-trips strings and the save converts it back.
const model = defineModel({ type: String, default: '' })

const { items, loading, error, load } = useGrainCandidates()
const search = ref('')

function refresh() {
  const side = props.parent === null
    ? { child: props.child }
    : { parent: props.parent }
  load({ ...side, q: search.value.trim() })
}

// A keystroke debounce, not a heuristic about state: it rate-limits input,
// and every answer it eventually asks for is still the server's.
let settle = null
watch(search, () => {
  clearTimeout(settle)
  settle = setTimeout(refresh, 140)
})
onMounted(refresh)
// The picker lives in a dialog, so it is routinely unmounted mid-keystroke.
// A pending timer that outlives it fires a request nobody will read, against
// a component that is gone.
onUnmounted(() => clearTimeout(settle))
</script>

<template>
  <div data-testid="task-parent-field">
    <span class="field-label">
      {{ label }}
      <span class="font-normal text-slate-400">({{ hint }})</span>
    </span>
    <Input
      v-model="search"
      type="text"
      placeholder="Find a task…"
      aria-label="Search parent candidates"
      data-testid="task-parent-search"
    />
    <Select
      v-model="model"
      block
      class="tpf-select"
      :options="[{ value: '', label: emptyLabel },
                 ...items.map(t => ({ value: String(t.id),
                                      label: candidateLabel(t) }))]"
      aria-label="Parent feature"
      data-testid="task-parent-select"
    />
    <p v-if="loading" class="tpf-note">reading the tasks that may be a parent…</p>
    <p v-else-if="error" class="tpf-note tpf-error" role="alert">{{ error }}</p>
    <p v-else-if="!items.length" class="tpf-note" data-testid="task-parent-empty">
      No task here may hold runs
      <template v-if="search.trim()"> under “{{ search.trim() }}”</template>.
    </p>
  </div>
</template>

<style scoped>
.tpf-select { margin-top: 0.375rem; }
.tpf-note {
    margin: 0.375rem 0 0; font-size: 11.5px; color: var(--color-fg-subtle);
}
.tpf-error { color: var(--color-danger); }
</style>
