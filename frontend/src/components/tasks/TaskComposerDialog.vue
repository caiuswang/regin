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
import TaskImageStrip from './TaskImageStrip.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { PRIORITIES } from '../../utils/taskMeta.js'
import { TITLE_MAX, emptyDraft, useTaskMutations }
  from '../../composables/useTasks.js'
import { imageFilesFrom } from '../../utils/imageFiles'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'
import { carriedProject, projectOptions } from '../../composables/useProjects.js'

const open = defineModel('open', { type: Boolean, default: false })
const props = defineProps({
  projects: { type: Array, default: () => [] },
  // The Tasks toolbar's project — a repo id, `none`, or `''` for every one.
  project: { type: String, default: '' },
})
const emit = defineEmits(['created'])

const { saving, formError, create } = useTaskMutations()
const draft = ref(emptyDraft())
const titleInput = ref(null)
// Bound to no task: a composer image has no row to hang off until the capture
// lands, so it is held in the browser and flushed against the new id.
const {
  items: images, busy: imagesBusy, error: imagesError,
  add: addImages, remove: removeImage, flush: flushImages, reset: resetImages,
} = useAttachedImages()

// The images cannot survive a close: they are browser blobs held against a
// task id that does not exist yet, and a thumbnail still sitting there on the
// next open would belong to an idea that was never captured.
watch(open, async (isOpen) => {
  if (!isOpen) {
    resetImages()
    return
  }
  // A starting value, and only on a fresh draft: a half-typed idea reopened
  // keeps the project the operator already picked for it.
  if (!draft.value.title && !draft.value.body) {
    draft.value.project_id = carriedProject(props.project, props.projects)
  }
  await nextTick()
  titleInput.value?.focus()
})

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
    wide
  >
    <form class="space-y-3" @submit.prevent="submit" @paste="onPaste"
          @dragover.prevent @drop="onDrop">
      <div>
        <label class="field-label" for="task-title">Title</label>
        <Input
          id="task-title"
          ref="titleInput"
          v-model="draft.title"
          type="text"
          :error="!!formError"
          :placeholder="`What should happen? (${TITLE_MAX} characters max)`"
          data-testid="task-title"
          @keyup.enter="submit"
        />
      </div>
      <div>
        <label class="field-label" for="task-body">
          Details <span class="font-normal text-slate-400">(optional)</span>
        </label>
        <RefTextarea
          id="task-body" ref="bodyArea" v-model="draft.body"
          :project="draft.project_id" :rows="4"
        />
      </div>
      <div class="tc-row">
        <div class="tc-col">
          <label class="field-label" for="task-tags">
            Tags <span class="font-normal text-slate-400">(comma separated)</span>
          </label>
          <Input id="task-tags" v-model="draft.tags" type="text" />
        </div>
        <div class="tc-col">
          <span class="field-label">Priority</span>
          <Select
            v-model="draft.priority"
            block
            :options="PRIORITIES.map(p => ({ value: p.value, label: p.label }))"
            aria-label="Priority"
            data-testid="task-composer-priority"
          />
        </div>
        <div class="tc-col">
          <span class="field-label">Project</span>
          <Select
            v-model="draft.project_id"
            block
            :options="projectOptions(projects)"
            aria-label="Project"
            data-testid="task-composer-project"
          />
        </div>
      </div>
      <div>
        <span class="field-label">
          Images <span class="font-normal text-slate-400">(optional)</span>
        </span>
        <TaskImageStrip
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
      <Button variant="secondary" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
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
