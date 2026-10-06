<script setup>
// Declaring a feature from the population page — and adopting one that already
// exists in a tracker.
//
// The spec field is offered here and not deferred to the ledger page because
// the thing worth writing down is what the feature is for, and it is never
// closer to hand than at the moment somebody decides to have one. It stays
// optional: a feature with a title and no spec is a real row, and refusing it
// would push the operator back to the command line the page exists to replace.
//
// Adopting is a MODE, not a second field. The two shapes have two writers of
// the title — a native feature's is typed here, an adopted one's comes from the
// fetch — so one input meaning either a title to write or a reference to
// resolve is exactly the ambiguity the modes remove. `POST /api/features` has
// carried `ref` since the endpoint existed; until now nothing in the browser
// could reach it, so an issue could only be adopted from the CLI.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import Select from '../ui/Select.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import TaskImageStrip from '../tasks/TaskImageStrip.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { carriedProject, projectOptions, useProjects }
  from '../../composables/useProjects.js'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'
import { imageFilesFrom } from '../../utils/imageFiles'

const open = defineModel('open', { type: Boolean, default: false })
// The Pipeline shell's `?project=` — a repo id, `none` or `all`. Only a repo
// id pre-selects; the other two mean nothing is known, so nothing is guessed.
const props = defineProps({ scope: { type: String, default: '' } })
const emit = defineEmits(['declared'])
const { items: projects, ensure: ensureProjects } = useProjects()

const mode = ref('declare')
const title = ref('')
const body = ref('')
const reference = ref('')
const provider = ref('')
const projectId = ref('')
const saving = ref(false)
const failed = ref('')
// Bound to no feature: the mockup is pasted before the row exists, so it is
// held in the browser and flushed against the new id. Nothing is staged
// server-side for a feature abandoned mid-typing, so there is no orphan.
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages('features')

const adopting = computed(() => mode.value === 'adopt')
const description = computed(() => (adopting.value
  ? 'The issue keeps its own title and its own key. regin fetches it once, and'
    + ' from then on the rounds, the cost and the defects are counted here.'
  : 'What is being delivered. The spec is thin prose — no file paths, which at'
    + ' this point would specify an implementation that does not exist yet.'))

// The typed draft outlives a close — an Escape is not a decision to throw the
// words away — and ends only when the feature is declared or the operator
// discards it, as the task composer's does. The images cannot survive a close:
// they are browser blobs held against a feature id that does not exist yet.
const pristine = computed(() => !title.value && !body.value && !reference.value && !provider.value)

function discard() {
  mode.value = 'declare'
  title.value = ''
  body.value = ''
  reference.value = ''
  provider.value = ''
  failed.value = ''
  projectId.value = ''
  resetImages()
}

watch(open, async (isOpen) => {
  if (!isOpen) {
    resetImages()
    return
  }
  failed.value = ''
  if (!pristine.value) return
  await ensureProjects()
  projectId.value = carriedProject(props.scope, projects.value)
})

const bodyArea = ref(null)

// Attaching is only half of it: a mockup the spec cannot name is a picture
// beside the spec rather than a picture in it, and `![](image:N)` is
// unguessable. The index a held file carries is allocated in the browser and
// asked for at flush, so the reference is writable before the feature exists.

async function takeImages(source, event) {
  const refs = imageRefBlock(await addImages(source))
  if (!refs) return
  body.value = withCaretInsert(
    caretTarget(event, bodyArea.value), body.value, refs)
}

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

// The empty title and the unresolvable reference are both refused by the
// server, and that refusal is the one this renders: a second rule in the page
// would be a second writer of what a feature needs, and the two drift.
async function declare() {
  saving.value = true
  failed.value = ''
  const payload = adopting.value
    ? { ref: reference.value.trim(), provider: provider.value.trim() }
    : { title: title.value, body: body.value }
  payload.project_id = projectId.value ? Number(projectId.value) : null
  const ledger = await api.post('/features', payload)
  saving.value = false
  if (ledger?.ok === false) {
    failed.value = ledger.msg || 'that feature was refused'
    return
  }
  // Uploaded before the caller reloads, so the new row renders already
  // carrying them. A declare that saved but could not take an image still
  // closes — the feature exists, and the strip's own error says which half
  // failed.
  await flushImages(ledger.id)
  discard()
  open.value = false
  emit('declared', ledger)
}
</script>

<template>
  <Dialog
    v-model:open="open"
    :title="adopting ? 'Adopt a tracker issue' : 'Declare a feature'"
    :description="description"
    kicker="New level · lands in spec"
    data-pl-part="dialog"
    data-testid="new-feature"
  >
    <template #mark><PlGlyph kind="dialog.feature" :scale="2" /></template>
    <div class="nf-modes" role="group" aria-label="How this feature comes into being">
      <Button
        :variant="adopting ? 'ghost' : 'secondary'"
        size="sm"
        data-pl-part="key"
        data-testid="mode-declare"
        :aria-pressed="!adopting"
        @click="mode = 'declare'"
      >Declare new</Button>
      <Button
        :variant="adopting ? 'secondary' : 'ghost'"
        size="sm"
        data-pl-part="key"
        data-testid="mode-adopt"
        :aria-pressed="adopting"
        @click="mode = 'adopt'"
      >Adopt a tracker issue</Button>
    </div>

    <form class="nf-form" @submit.prevent="declare" @paste="onPaste"
          @dragover.prevent @drop="onDrop">
      <template v-if="adopting">
        <label class="nf-label" for="nf-ref" data-pl-part="field-label">Issue reference</label>
        <Input
          id="nf-ref"
          v-model="reference"
          data-testid="adopt-ref"
          data-pl-part="input"
          placeholder="CAI-274, or a full issue URL"
        />

        <label class="nf-label" for="nf-provider" data-pl-part="field-label">Provider</label>
        <Input
          id="nf-provider"
          v-model="provider"
          data-testid="adopt-provider"
          data-pl-part="input"
          placeholder="leave blank to let the reference decide"
        />
      </template>

      <template v-else>
        <label class="nf-label" for="nf-title" data-pl-part="field-label">Title</label>
        <Input
          id="nf-title"
          v-model="title"
          data-testid="declare-title"
          data-pl-part="input"
          placeholder="what is being delivered"
        />

        <label class="nf-label" for="nf-body" data-pl-part="field-label">Spec</label>
        <RefTextarea
          id="nf-body"
          ref="bodyArea"
          v-model="body"
          :project="projectId"
          data-testid="declare-body"
          data-pl-part="input"
          :rows="8"
          placeholder="Intent, non-goals, and the acceptance items — as test names"
        />
      </template>

      <span class="nf-label" data-pl-part="field-label">Project</span>
      <Select
        v-model="projectId"
        block
        :options="projectOptions(projects)"
        aria-label="Project"
        data-pl-part="input"
        data-testid="declare-project"
      />

      <span class="nf-label" data-pl-part="field-label">Images <span class="nf-optional">(optional)</span></span>
      <TaskImageStrip
        data-pl-part="drop"
        :items="images"
        :busy="imagesBusy"
        :error="imagesError"
        @pick="takeImages"
        @remove="removeImage"
      />

      <p v-if="failed" class="nf-fail" role="alert">{{ failed }}</p>
    </form>

    <template #footer>
      <Button
        v-if="!pristine"
        variant="ghost"
        data-pl-part="key"
        data-testid="declare-discard"
        @click="discard(); open = false"
      >Discard draft</Button>
      <Button variant="ghost" data-pl-part="key" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        data-pl-part="key--primary"
        data-pl-mark="⚑"
        data-testid="declare-submit"
        :disabled="saving"
        @click="declare"
      >{{ adopting ? 'Adopt' : 'Declare' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.nf-modes { display: flex; gap: 0.35rem; margin-bottom: 0.4rem; }
.nf-form { display: flex; flex-direction: column; gap: 0.35rem; }
.nf-label {
    font-size: 11.5px; font-weight: 600; color: var(--color-fg-subtle);
    margin-top: 0.4rem;
}
.nf-optional { font-weight: 400; color: var(--color-fg-faint); }
.nf-fail {
    margin-top: 0.5rem; font-size: 12px; color: var(--color-red-700);
}
</style>
