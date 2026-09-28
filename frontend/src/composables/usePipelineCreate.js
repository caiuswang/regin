import { ref } from 'vue'

// Module-level, because the writer and the reader are different components:
// a tab's button or `c` chord asks, and the shell — which owns the three
// dialogs, so every tab can create every kind — answers.
const creating = ref(null)

export const CREATE_KINDS = [
  { kind: 'task', label: 'Task' },
  { kind: 'feature', label: 'Feature' },
  { kind: 'defect', label: 'Defect' },
]

export function usePipelineCreate() {
  function openCreate(kind) {
    creating.value = kind
  }
  return { creating, openCreate }
}
