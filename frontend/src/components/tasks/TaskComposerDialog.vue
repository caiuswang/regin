<script setup>
// Capture, as a dialog rather than a card pinned above the list.
//
// The old composer stood permanently expanded at the top of /tasks and cost
// the first screenful of every visit, on a page whose job is reading a
// backlog. Behind a button and a `c` chord it costs nothing until it is
// wanted, and the list starts at the top of the page.
//
// Wider than QuickCaptureBubble on purpose: this is the curation surface, so
// it carries the fields — tags, priority, project — the bubble deliberately
// omits to stay a one-thought capture.
import { nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import PlSlot from '../pipeline/PlSlot.vue'
import FeaturePicker from '../pipeline/FeaturePicker.vue'
import TaskImageStrip from './TaskImageStrip.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { PRIORITIES } from '../../utils/taskMeta.js'
import { TITLE_MAX, emptyDraft, useTaskMutations }
  from '../../composables/useTasks.js'
import { imageFilesFrom } from '../../utils/imageFiles'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'
import { carriedProject, projectOptions, useProjects }
  from '../../composables/useProjects.js'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  projects: { type: Array, default: () => [] },
  // The Tasks toolbar's project — a repo id, `none`, or `''` for every one.
  project: { type: String, default: '' },
  // `{ id, title, project_id? }` of the feature the opening page names — a
  // chip, not a lock. A `project_id` key, even null, is the feature's own
  // project and outranks the toolbar's: a step is filed where its feature is.
  feature: { type: Object, default: null },
})
const emit = defineEmits(['created'])

// A theme may draw these as keys, so each carries its bar glyph too.
const PRIORITY_CHOICES = PRIORITIES.map(p => ({ value: p.value, label: p.label, glyph: `bars.${p.bars}` }))
// A free-form step may declare any stage but fix: a fix step belongs to a
// defect and is made from the defect, never typed here.
const STAGE_CHOICES = [
  { value: 'task', label: 'Task' },
  { value: 'spec', label: 'Spec' },
  { value: 'build', label: 'Build' },
]

const { saving, formError, create } = useTaskMutations()
const { items: projectList, ensure: ensureProjects } = useProjects()
const draft = ref(emptyDraft())
const titleInput = ref(null)
// Bound to no task: a composer image has no row to hang off until the capture
// lands, so it is held in the browser and flushed against the new id.
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages()

// The dialog outlives the page that prefilled it, so a prefill the operator
// left untouched is withdrawn when a page naming no feature opens it again.
let prefilled = ''
let placedProject = null
// The images cannot survive a close: they are browser blobs held against a
// task id that does not exist yet, and a thumbnail still sitting there on the
// next open would belong to an idea that was never captured.
watch(open, async (isOpen) => {
  if (!isOpen) {
    resetImages()
    return
  }
  const was = draft.value.feature_id
  if (props.feature?.id) {
    draft.value.feature_id = prefilled = String(props.feature.id)
  } else if (prefilled && draft.value.feature_id === prefilled) {
    draft.value.feature_id = prefilled = ''
  }
  const fresh = !draft.value.title && !draft.value.body
  // A project this dialog chose follows the feature it was chosen for; one the
  // operator picked stays theirs.
  const ours = placedProject !== null && draft.value.project_id === placedProject
  await nextTick()
  titleInput.value?.focus()
  // A starting value: a half-typed idea reopened keeps the project the
  // operator already picked for it. Read off the list once it has loaded, as
  // NewFeatureDialog does — a dialog opened before the repos answered would
  // otherwise carry nothing, and its `@` would search every project.
  if (!fresh && !(draft.value.feature_id !== was && ours)) return
  const before = draft.value.project_id
  const start = props.feature && 'project_id' in props.feature
    ? props.feature.project_id : props.project
  await ensureProjects()
  // A pick made while the list was loading is the operator's, not ours.
  if (draft.value.project_id === before) {
    draft.value.project_id = placedProject = carriedProject(start, projectList.value)
  }
})
// The stage is a step's place in ONE feature: moved to another, or cleared,
// it would name a step nobody chose for it.
watch(() => draft.value.feature_id, () => { draft.value.kind = 'task' })

const bodyArea = ref(null)

// Attaching is only half of it: the bytes are useless to a reader until the
// prose names them, and `![](image:N)` is unguessable. The index a held file
// carries is allocated in the browser and asked for at flush, so the reference
// is writable at the moment of the paste rather than after the row exists.
async function takeImages(source, event) {
  const refs = imageRefBlock(await addImages(source))
  if (!refs) return
  draft.value.body = withCaretInsert(
    caretTarget(event, bodyArea.value), draft.value.body, refs)
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

// The images are uploaded BEFORE the caller reloads, so the new row renders
// already carrying them. A capture that saved but could not take one of its
// images still clears the draft — the task exists, and the strip's own error
// says which half failed.
async function submit() {
  const task = await create(draft.value)
  if (!task) return
  await flushImages(task.id)
  reset()
  open.value = false
  emit('created', task)
}

function reset() {
  draft.value = emptyDraft()
  resetImages()
}
</script>

<template>
  <Dialog
    v-model:open="open"
    title="New task"
    description="Capture an idea. Nothing runs until someone arms it."
    kicker="New quest · lands in ideas"
    hint="Enter capture · Esc close"
    wide
    data-pl-part="dialog"
    data-testid="task-composer"
  >
    <template #mark><PlGlyph kind="dialog.task" :scale="2" /></template>
    <form class="space-y-3" @submit.prevent="submit" @paste="onPaste"
          @dragover.prevent @drop="onDrop">
      <div>
        <label class="field-label" for="task-title" data-pl-part="field-label"
               :data-pl-count="`${draft.title.length}/${TITLE_MAX}`">Title</label>
        <Input
          id="task-title"
          ref="titleInput"
          v-model="draft.title"
          type="text"
          :error="!!formError"
          :placeholder="`What should happen? (${TITLE_MAX} characters max)`"
          data-testid="task-title"
          data-pl-part="input"
          @keyup.enter="submit"
        />
      </div>
      <div>
        <label class="field-label" for="task-body" data-pl-part="field-label">
          Details <span class="font-normal text-slate-400">(optional)</span>
        </label>
        <RefTextarea
          id="task-body" ref="bodyArea" v-model="draft.body" data-pl-part="input"
          :project="draft.project_id" :rows="4"
        />
      </div>
      <div class="tc-row" data-pl-part="field-row">
        <div class="tc-col" data-pl-part="field" data-pl-field="tags">
          <label class="field-label" for="task-tags" data-pl-part="field-label">
            Tags <span class="font-normal text-slate-400">(comma separated)</span>
          </label>
          <Input id="task-tags" v-model="draft.tags" type="text" data-pl-part="input" />
        </div>
        <div class="tc-col" data-pl-part="field" data-pl-field="priority">
          <span class="field-label" data-pl-part="field-label">Priority</span>
          <PlSlot
            v-model="draft.priority"
            name="ChoiceField"
            :options="PRIORITY_CHOICES"
            aria-label="Priority"
            data-testid="task-composer-priority"
          />
        </div>
        <div class="tc-col" data-pl-part="field" data-pl-field="project">
          <span class="field-label" data-pl-part="field-label">Project</span>
          <Select
            data-pl-part="input"
            v-model="draft.project_id"
            block
            :options="projectOptions(projects)"
            aria-label="Project"
            data-testid="task-composer-project"
          />
        </div>
      </div>
      <div>
        <span class="field-label" data-pl-part="field-label">
          Feature <span class="font-normal text-slate-400">(optional)</span>
        </span>
        <FeaturePicker
          v-model="draft.feature_id" :current="feature"
          label="The feature this task is a step of" testid="task-composer-feature"
        />
      </div>
      <div v-if="draft.feature_id" data-pl-part="field" data-pl-field="stage">
        <span class="field-label" data-pl-part="field-label">Stage</span>
        <PlSlot
          v-model="draft.kind"
          name="ChoiceField"
          :options="STAGE_CHOICES"
          aria-label="Stage"
          data-testid="task-composer-stage"
        />
      </div>
      <div>
        <span class="field-label" data-pl-part="field-label">
          Images <span class="font-normal text-slate-400">(optional)</span>
        </span>
        <TaskImageStrip
          data-pl-part="drop"
          :items="images"
          :busy="imagesBusy"
          :error="imagesError"
          @pick="takeImages"
          @remove="removeImage"
        />
      </div>
      <p v-if="formError" class="tc-error" role="alert" data-testid="task-form-error">
        {{ formError }}
      </p>
    </form>

    <template #footer>
      <Button variant="secondary" data-pl-part="key" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        data-pl-part="key--primary"
        data-pl-mark="✦"
        :disabled="saving || !draft.title.trim()"
        data-testid="task-submit"
        @click="submit"
      >
        Capture idea
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
.tc-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
}
.tc-col { flex: 1 1 10rem; min-width: 0; }
.tc-error {
    font-size: 0.8125rem;
    color: var(--color-danger);
}
</style>
