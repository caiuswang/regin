<script setup>
// Report a defect from the Pipeline's "New ▾" menu.
//
// The feature is optional on purpose: a report blamed on nothing is what the
// triage queue exists for, and a picker that nudged the operator toward a
// guess would file a wrong parent — which inflates one feature's rounds and
// exonerates another. Nothing is preselected unless the page it was opened
// from already names the feature (a defect's own page).
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Textarea from '../ui/Textarea.vue'
import MarkdownEditor from '../editor/MarkdownEditor.vue'
import FeaturePicker from '../pipeline/FeaturePicker.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import TaskImageStrip from '../tasks/TaskImageStrip.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { imageFilesFrom } from '../../utils/imageFiles'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  // `{ id, title }` — shown as the picker's chip, so it reads as a choice.
  feature: { type: Object, default: null },
})
const emit = defineEmits(['created'])

const symptom = ref('')
const featureId = ref('')
const paths = ref('')
const saving = ref(false)
const failed = ref('')
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages('defects')

// A defect has no project of its own; it is its feature's. Named only while
// the chosen feature is the one the page handed over, whose project is known.
const featureProject = computed(() => (props.feature && 'project_name' in props.feature
  && featureId.value === String(props.feature.id)
  ? props.feature.project_name || 'No project' : null))

const suspectPaths = computed(() => paths.value.split('\n').map(p => p.trim()).filter(Boolean))

function reset() {
  symptom.value = ''
  featureId.value = ''
  paths.value = ''
  failed.value = ''
  resetImages()
}

// The dialog outlives the page that prefilled it, so a prefill the operator
// left untouched is withdrawn when a page naming no feature opens it again.
let prefilled = ''
watch(open, (isOpen) => {
  if (!isOpen) {
    resetImages()
  } else if (props.feature?.id) {
    featureId.value = prefilled = String(props.feature.id)
  } else if (prefilled && featureId.value === prefilled) {
    featureId.value = prefilled = ''
  }
})

async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await addImages(e.clipboardData)
}

// The empty symptom is the server's to refuse (`symptom is required`), and
// that sentence is the one rendered — a second rule here would drift from it.
async function submit() {
  if (saving.value) return
  saving.value = true
  failed.value = ''
  try {
    const payload = { symptom: symptom.value, suspect_paths: suspectPaths.value }
    if (featureId.value) payload.feature_id = Number(featureId.value)
    const result = await api.post('/defects', payload)
    if (result?.ok === false) {
      failed.value = result.msg || 'the report was refused'
      return
    }
    await flushImages(result.defect.id)
    reset()
    open.value = false
    emit('created', result)
  } catch (e) {
    failed.value = e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:open="open"
    title="New defect"
    description="What went wrong. Leave the feature empty when you do not know which one caused it — the report goes to triage."
    kicker="Wild bug · lands in triage"
    data-pl-part="dialog"
  >
    <template #mark><PlGlyph kind="defect.wild" :scale="2" /></template>
    <form class="nd-form" data-testid="new-defect" @submit.prevent="submit" @paste="onPaste">
      <label class="nd-label" for="nd-symptom" data-pl-part="field-label">Symptom</label>
      <MarkdownEditor
        id="nd-symptom"
        v-model="symptom"
        :feature="featureId ? Number(featureId) : null"
        :expandable="false" :outline="false"
        preview-testid="new-defect-symptom"
        class="nd-symptom"
        data-testid="new-defect-symptom"
        data-pl-part="input"
        aria-label="Defect symptom markdown"
        :rows="4"
        placeholder="What you saw, and where"
        @submit="submit"
      />

      <span class="nd-label" data-pl-part="field-label">Feature <span class="nd-optional">(optional — none sends it to triage)</span></span>
      <FeaturePicker v-model="featureId" :current="feature" label="The feature that caused this" testid="new-defect-feature" />
      <p v-if="featureProject" class="nd-project" data-testid="new-defect-project">
        Project: {{ featureProject }} <span class="nd-optional">(the feature's)</span>
      </p>

      <label class="nd-label" for="nd-paths" data-pl-part="field-label">Suspect paths <span class="nd-optional">(one per line)</span></label>
      <Textarea
        id="nd-paths"
        v-model="paths"
        :rows="2"
        data-testid="new-defect-paths"
        data-pl-part="input"
        placeholder="frontend/src/views/…"
      />

      <span class="nd-label" data-pl-part="field-label">Images <span class="nd-optional">(optional)</span></span>
      <TaskImageStrip
        data-pl-part="drop"
        :items="images"
        :busy="imagesBusy"
        :error="imagesError"
        @pick="addImages"
        @remove="removeImage"
      />

      <p v-if="failed" class="nd-fail" role="alert" data-testid="new-defect-error">{{ failed }}</p>
    </form>

    <template #footer>
      <Button variant="ghost" data-pl-part="key" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        data-pl-part="key--primary"
        data-testid="new-defect-submit"
        :disabled="saving"
        @click="submit"
      >Report</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.nd-form { display: flex; flex-direction: column; gap: 0.35rem; }
.nd-label { font-size: 11.5px; font-weight: 600; color: var(--color-fg-subtle); margin-top: 0.4rem; }
.nd-symptom { margin-top: 0; }
.nd-optional { font-weight: 400; color: var(--color-fg-faint); }
.nd-project { margin: 0; font-size: 11.5px; color: var(--color-fg-muted); }
.nd-fail { margin-top: 0.5rem; font-size: 12px; color: var(--color-danger); }
</style>
