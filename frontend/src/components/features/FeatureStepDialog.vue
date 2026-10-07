<script setup>
// A feature's Spec or Build step, opened as a dialog like every other create.
//
// It is the feature's own verb, not a free-form task: the server keys it
// `feature:<id>:<kind>`, so there is one per feature, and the page that asked
// has already turned an existing step into a link before this opens.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import RefTextarea from '../refs/RefTextarea.vue'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  featureId: { type: Number, required: true },
  project: { type: Number, default: null },
  kind: { type: String, default: 'spec' },
})
const router = useRouter()

const body = ref('')
const busy = ref(false)
const failed = ref('')
const isSpec = computed(() => props.kind === 'spec')

watch(open, (isOpen) => {
  if (!isOpen) return
  body.value = ''
  failed.value = ''
})

async function submit() {
  if (busy.value) return
  busy.value = true
  failed.value = ''
  try {
    const res = await api.post(`/features/${props.featureId}/steps`, {
      kind: props.kind, body: body.value.trim() || null,
    })
    if (res?.ok === false || !res?.task) {
      failed.value = res?.msg || 'the step was refused'
      return
    }
    open.value = false
    router.push(`/tasks/${res.task.id}`)
  } catch (e) {
    failed.value = e?.message || String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:open="open"
    :title="isSpec ? 'Write spec' : 'Build'"
    :description="isSpec
      ? 'A spec step for this feature. Arm and run it on its task page.'
      : 'The build step for this feature. Arm and run it on its task page.'"
    data-pl-part="dialog"
    data-testid="feature-step-dialog"
  >
    <form class="fs-form" @submit.prevent="submit">
      <label class="fs-label" :for="`fs-body-${featureId}`" data-pl-part="field-label">
        Instructions <span class="fs-optional">(optional)</span>
      </label>
      <RefTextarea
        :id="`fs-body-${featureId}`"
        v-model="body" :project="project" :feature="featureId" :rows="4" :disabled="busy"
        :placeholder="isSpec ? 'what the spec must settle' : 'what the build must respect'"
        data-pl-part="input"
        data-testid="feature-step-body"
      />
      <p v-if="failed" class="fs-fail" role="alert" data-testid="feature-step-error">{{ failed }}</p>
    </form>

    <template #footer>
      <Button variant="ghost" data-pl-part="key" :disabled="busy" @click="open = false">Cancel</Button>
      <Button
        variant="primary" data-pl-part="key--primary" :disabled="busy"
        data-testid="feature-step-submit" @click="submit"
      >{{ busy ? 'Creating…' : 'Create' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.fs-form { display: flex; flex-direction: column; gap: 0.35rem; }
.fs-label { font-size: 11.5px; font-weight: 600; color: var(--color-fg-subtle); }
.fs-optional { font-weight: 400; color: var(--color-fg-faint); }
.fs-fail { margin-top: 0.5rem; font-size: 12px; color: var(--color-danger); }
</style>
