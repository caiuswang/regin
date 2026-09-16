<script setup>
// Naming an existing task as a run of this one, from the side the operator is
// standing on when they realise it.
//
// The same picker as the edit sheet, asked against the other side of the
// relationship, and the same PATCH — `parent_id` on the task being attached.
// The refusal rendered here is the server's own sentence: it names both rows,
// and re-wording it would hide which rule refused.
import { ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import TaskParentField from './TaskParentField.vue'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'attached'])

const chosen = ref('')
const saving = ref(false)
const attachError = ref('')

async function attach() {
  if (!chosen.value) return
  saving.value = true
  const result = await api.patch(`/tasks/${chosen.value}`,
                                 { parent_id: props.task.id })
  saving.value = false
  if (result.ok === false) {
    attachError.value = result.msg || 'The server refused this parentage.'
    return
  }
  emit('attached')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Attach a run"
    :description="`Name a task as a run of #${task.id}. Its sessions are then `
      + 'counted as sessions of this feature.'"
    @update:open="v => !v && emit('close')"
  >
    <TaskParentField
      v-model="chosen"
      :parent="task.id"
      label="Task to attach"
      hint="becomes a run of this task"
      empty-label="Nothing chosen yet"
    />
    <p v-if="attachError" class="tar-error" role="alert"
       data-testid="task-attach-error">
      {{ attachError }}
    </p>

    <template #footer>
      <Button variant="ghost" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || !chosen"
        data-testid="task-attach-save"
        @click="attach"
      >
        {{ saving ? 'Attaching…' : 'Attach as a run' }}
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
.tar-error {
    margin-top: 0.75rem; font-size: 0.8125rem; color: var(--color-danger);
}
</style>
