<script setup>
// A feature's step for one of its project's stages, opened as a dialog like
// every other create.
//
// It is the feature's own step, not a free-form task: the server keys it
// `feature:<id>:<stage>`, so there is one per stage, and the page that asked
// has already turned an existing step into a link before this opens.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import MarkdownEditor from '../editor/MarkdownEditor.vue'
import PromptBody from '../PromptBody.vue'
import TaskImageStrip from '../tasks/TaskImageStrip.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { imageFilesFrom } from '../../utils/imageFiles'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  featureId: { type: Number, required: true },
  project: { type: Number, default: null },
  // A project stage view: `{ key, label, role, prompt_body }`.
  stage: { type: Object, default: null },
})
const router = useRouter()

const body = ref('')
const busy = ref(false)
const failed = ref('')
const label = computed(() => props.stage?.label || 'Step')
const isSpec = computed(() => props.stage?.role !== 'build')
const prompt = computed(() => (props.stage?.prompt_body || '').trim())
// The step task does not exist until Create, so a pasted image is held in the
// browser and flushed against the id the server answers with.
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages('tasks')

watch(open, (isOpen) => {
  resetImages()
  if (!isOpen) return
  body.value = ''
  failed.value = ''
})

const bodyArea = ref(null)

async function takeImages(source, event) {
  bodyArea.value?.insertImages(await addImages(source), event)
}

const attach = (source) => addImages(source)

async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await takeImages(e.clipboardData, e)
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await takeImages(e.dataTransfer, e)
}

async function submit() {
  if (busy.value || !props.stage) return
  busy.value = true
  failed.value = ''
  try {
    const res = await api.post(`/features/${props.featureId}/steps`, {
      stage: props.stage.key, body: body.value.trim() || null,
    })
    if (res?.ok === false || !res?.task) {
      failed.value = res?.msg || 'the step was refused'
      return
    }
    // An existing step keeps its own body, so nothing in it would cite these.
    if (res.created !== false) await flushImages(res.task.id)
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
    :title="label"
    :description="`The ${label} step for this feature. Arm and run it on its task page.`"
    data-pl-part="dialog"
    data-testid="feature-step-dialog"
  >
    <form class="fs-form" @submit.prevent="submit" @paste="onPaste" @dragover.prevent @drop="onDrop">
      <div class="fs-prompt" data-testid="feature-step-prompt">
        <span class="fs-label" data-pl-part="field-label">Stage prompt</span>
        <div v-if="prompt" class="fs-prompt__body"><PromptBody :text="prompt" /></div>
        <p v-else class="fs-optional" data-testid="feature-step-prompt-empty">
          This stage has no prompt of its own.
        </p>
      </div>
      <label class="fs-label" :for="`fs-body-${featureId}`" data-pl-part="field-label">
        Instructions <span class="fs-optional">(optional)</span>
      </label>
      <MarkdownEditor
        :id="`fs-body-${featureId}`"
        ref="bodyArea"
        v-model="body" :project="project" :feature="featureId"
        :images="images" :attach="attach"
        :expandable="false" :outline="false" mode-scope="compose"
        preview-testid="feature-step-body-rich"
        :rows="4" :disabled="busy"
        :placeholder="isSpec ? 'what the spec must settle' : 'what the build must respect'"
        aria-label="Step instructions markdown"
        data-pl-part="input"
        data-testid="feature-step-body"
        @submit="submit"
        @attach-error="failed = $event"
      />
      <TaskImageStrip
        v-if="images.length || imagesError"
        data-pl-part="drop"
        :items="images" :busy="imagesBusy" :error="imagesError"
        @pick="takeImages" @remove="removeImage"
      />
      <p v-if="failed" class="fs-fail" role="alert" data-testid="feature-step-error">{{ failed }}</p>
    </form>

    <template #footer>
      <Button variant="ghost" data-pl-part="key" :disabled="busy" @click="open = false">Cancel</Button>
      <Button
        variant="primary" data-pl-part="key--primary" :disabled="busy || !stage"
        data-testid="feature-step-submit" @click="submit"
      >{{ busy ? 'Creating…' : 'Create' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.fs-form { display: flex; flex-direction: column; gap: 0.35rem; }
.fs-label { font-size: 11.5px; font-weight: 600; color: var(--color-fg-subtle); }
.fs-optional { font-weight: 400; font-size: 11.5px; color: var(--color-fg-faint); }
.fs-prompt { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.5rem; }
.fs-prompt__body {
    padding: 0.4rem 0.55rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
    background: var(--color-surface-2); font-size: 12px;
}
.fs-fail { margin-top: 0.5rem; font-size: 12px; color: var(--color-danger); }
</style>
