<script setup>
// Re-open a saved idea: its wording, its tags, its filing, and its images.
//
// The text and the images settle on different clocks, and that is deliberate.
// Text is a draft until Save PATCHes it, so Cancel really cancels. An image is
// written the moment it is pasted, because the task row already exists to hang
// it off and a "saved" thumbnail that vanishes on Cancel is a worse lie than
// one that appears immediately — the strip shows what the task carries right
// now, which is also what its next run will deliver.
import { onMounted, ref } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import FeaturePicker from '../pipeline/FeaturePicker.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import AddProjectLink from '../projects/AddProjectLink.vue'
import TaskImageStrip from './TaskImageStrip.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { PRIORITIES } from '../../utils/taskMeta.js'
import { imageFilesFrom } from '../../utils/imageFiles'
import { useProjects } from '../../composables/useProjects.js'
import { useUsers } from '../../composables/useUsers.js'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'
import { useTaskEdit } from '../../composables/useTaskEdit.js'

const props = defineProps({
  task: { type: Object, required: true },
})
const emit = defineEmits(['close', 'saved'])

const { draft, saving, editError, save } = useTaskEdit(props.task)
const { items: projects, ensure: ensureProjects } = useProjects()
const { items: users, ensure: ensureUsers } = useUsers()

// A fix step is owned by its defect and only `fix_items.create_for` makes one,
// so the sheet neither turns a task into a fix nor a fix into anything else.
const isFix = props.task.kind === 'fix'
const KIND_OPTIONS = (isFix ? ['fix'] : ['task', 'spec', 'build'])
  .map(k => ({ value: k, label: k }))
const currentFeature = props.task.feature_id == null ? null
  : { id: props.task.feature_id, title: props.task.feature_title || '' }
const {
  items: imageItems, busy: imageBusy, error: imageError,
  bind: bindImages, add: addImages, remove: removeImage,
} = useAttachedImages()

onMounted(() => {
  bindImages(props.task)
  ensureProjects()
  ensureUsers()
})

// Paste and drop are bound on the form rather than on the strip: an operator
// pastes into the field they are typing in, and the event bubbles to here.
const bodyArea = ref(null)

// Attaching is only half of it: the bytes are useless to a reader until the
// prose names them, and `![](image:N)` is unguessable. The task is saved, so
// the index here is the server's own from the moment the upload returns.

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

async function submit() {
  if (await save()) emit('saved')
}
</script>

<template>
  <Dialog
    :open="true"
    title="Edit idea"
    description="The wording travels to the agent as the prompt; the images ride beside it."
    @update:open="v => !v && emit('close')"
  >
    <form
      class="task-edit-form"
      data-testid="task-edit-form"
      @submit.prevent="submit"
      @paste="onPaste"
      @dragover.prevent
      @drop="onDrop"
    >
      <div>
        <label class="field-label" for="task-edit-title">Title</label>
        <Input
          id="task-edit-title"
          v-model="draft.title"
          type="text"
          :error="!!editError"
          aria-label="Task title"
        />
      </div>
      <div>
        <label class="field-label" for="task-edit-body">Details</label>
        <RefTextarea
          id="task-edit-body"
          ref="bodyArea"
          v-model="draft.body"
          :project="draft.project_id"
          :feature="draft.feature_id"
          :rows="4"
          aria-label="Task details"
        />
      </div>
      <div>
        <label class="field-label" for="task-edit-tags">
          Tags <span class="font-normal text-slate-400">(comma separated)</span>
        </label>
        <Input
          id="task-edit-tags"
          v-model="draft.tags"
          type="text"
          aria-label="Task tags"
        />
      </div>
      <div class="task-edit-row">
        <div class="task-edit-col">
          <span class="field-label">Priority</span>
          <Select
            v-model="draft.priority"
            block
            :options="PRIORITIES.map(p => ({ value: p.value, label: p.label }))"
            aria-label="Task priority"
            data-testid="task-edit-priority"
          />
        </div>
        <div class="task-edit-col">
          <span class="field-label">Project
            <AddProjectLink v-if="!projects.length" testid="task-edit-add-project" @added="a => { if (a.project) draft.project_id = String(a.project.id) }" />
          </span>
          <Select
            v-model="draft.project_id"
            block
            :options="[{ value: '', label: 'No project' },
                       ...projects.map(p => ({ value: String(p.id),
                                               label: p.name }))]"
            aria-label="Task project"
            data-testid="task-edit-project"
          />
        </div>
      </div>
      <div class="task-edit-row">
        <div class="task-edit-col">
          <span class="field-label">Kind</span>
          <Select
            v-model="draft.kind"
            block
            :options="KIND_OPTIONS"
            :disabled="isFix"
            aria-label="Task kind"
            data-testid="task-edit-kind"
          />
        </div>
        <div class="task-edit-col">
          <span class="field-label">Assignee</span>
          <Select
            v-model="draft.assignee"
            block
            :options="[{ value: '', label: 'Unassigned' },
                       ...users.map(u => ({ value: String(u.id), label: u.name }))]"
            aria-label="Task assignee"
            data-testid="task-edit-assignee"
          />
        </div>
      </div>
      <div>
        <span class="field-label">Feature</span>
        <FeaturePicker
          v-model="draft.feature_id"
          :current="currentFeature"
          label="The feature this task is a step of"
          placeholder="No feature — search to file it under one"
          testid="task-edit-feature"
        />
      </div>
      <div>
        <span class="field-label">Images</span>
        <TaskImageStrip
          :items="imageItems"
          :busy="imageBusy"
          :error="imageError"
          @pick="takeImages"
          @remove="removeImage"
        />
      </div>
    </form>

    <p v-if="editError" class="task-edit-error" role="alert" data-testid="task-edit-error">
      {{ editError }}
    </p>

    <template #footer>
      <Button variant="ghost" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || !draft.title.trim()"
        data-testid="task-edit-save"
        @click="submit"
      >
        {{ saving ? 'Saving…' : 'Save changes' }}
      </Button>
    </template>
  </Dialog>
</template>

<style scoped>
.task-edit-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    min-width: 0;
}
.task-edit-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
}
.task-edit-col { flex: 1 1 10rem; min-width: 0; }
.task-edit-error {
    margin-top: 0.75rem;
    font-size: 0.8125rem;
    color: var(--color-danger);
}
</style>
