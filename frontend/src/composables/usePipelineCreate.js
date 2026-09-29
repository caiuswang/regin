import { ref } from 'vue'

// Module-level, because the writer and the reader are different components:
// a tab's button or `c` chord asks, and the shell — which owns the three
// dialogs, so every tab can create every kind — answers.
const creating = ref(null)
// What the page that asked already knows about the row to create — a defect
// page names its feature. Cleared by every ask that states none.
const prefill = ref(null)

export const CREATE_KINDS = [
  { kind: 'task', label: 'Task' },
  { kind: 'feature', label: 'Feature' },
  { kind: 'defect', label: 'Defect' },
]

export function usePipelineCreate() {
  function openCreate(kind, given = null) {
    prefill.value = given
    creating.value = kind
  }
  return { creating, prefill, openCreate }
}
