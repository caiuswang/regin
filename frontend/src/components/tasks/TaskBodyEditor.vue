<script setup>
// The task body, read and written on the page that has its address.
//
// Edited on its rendering, with source one click away (`editor/MarkdownEditor`
// says how): an untouched block goes back as the exact bytes it was given.
//
// Shaped after `PatternContentEditor`, the same read-then-Edit toggle.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import MarkdownEditor from '../editor/MarkdownEditor.vue'
import { useDiscardGuard } from '../../composables/useDiscardGuard'
import { imageFilesFrom } from '../../utils/imageFiles'
import { IMAGE_REF_HINT, resolveImageRefs } from '../../utils/taskBodyImages'

const props = defineProps({
  task: { type: Object, required: true },
  images: { type: Array, default: () => [] },
  canWrite: { type: Boolean, default: false },
  // Returns the `idx` of every image it attached, which is what the reference
  // written back into the text is made of.
  attach: { type: Function, required: true },
  emptyText: { type: String, default: '' },
})
const emit = defineEmits(['saved'])

const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const error = ref('')
const area = ref(null)

const rendered = computed(() => resolveImageRefs(props.task.body, props.images))

function start() {
  draft.value = props.task.body || ''
  error.value = ''
  editing.value = true
}

const { confirmDiscard } = useDiscardGuard(() => editing.value && draft.value !== (props.task.body || ''))

async function cancel() {
  if (!(await confirmDiscard())) return
  editing.value = false
  error.value = ''
}

async function save() {
  saving.value = true
  // The server's own sentence, not a reworded one: `parse_update` names the
  // field it refused and the body is the only field in flight here.
  const res = await api.patch(`/tasks/${props.task.id}`, { body: draft.value })
  saving.value = false
  if (res.ok === false) {
    error.value = res.msg || 'could not save'
    return
  }
  editing.value = false
  emit('saved')
}

// Attaching, then writing the reference: the syntax is otherwise unguessable,
// and an attachment nobody can name is the defect this fixes.
async function takeImages(source, event) {
  area.value?.insertImages(await props.attach(source), event)
}

async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await takeImages(e.clipboardData, e)
}

// Drop is paste's neighbour and reaches the files a screenshot tool wrote to
// disk rather than to the clipboard. It has no caret of its own, so the
// reference lands at wherever the textarea was last left.
async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await takeImages(e.dataTransfer, e)
}
</script>

<template>
  <Card>
    <div class="tbe-head">
      <h2 class="card-header tbe-title">Details</h2>
      <div v-if="!editing && canWrite">
        <Button variant="ghost" size="sm" data-testid="task-body-edit" @click="start">
          <Icon name="pencil" :size="13" />
          <span>Edit</span>
        </Button>
      </div>
    </div>

    <template v-if="editing">
      <MarkdownEditor
        ref="area"
        v-model="draft" :project="task.project_id" :feature="task.feature_id"
        :images="images" :attach="attach"
        preview-testid="task-body-rich" :title="task.title || ''"
        class="tbe-editor"
        :rows="18" spellcheck="false"
        aria-label="Task details markdown" data-testid="task-body-area"
        @paste="onPaste"
        @dragover.prevent
        @drop="onDrop"
        @submit="save"
      >
        <template #actions>
          <Button
            variant="primary" size="sm" :disabled="saving"
            data-testid="task-body-save" @click="save"
          >{{ saving ? 'Saving…' : 'Save' }}</Button>
          <Button variant="secondary" size="sm" data-testid="task-body-cancel" @click="cancel">
            Cancel
          </Button>
        </template>
      </MarkdownEditor>
      <p class="tbe-hint">Markdown. {{ IMAGE_REF_HINT }} Paste or drop an image here.</p>
      <p v-if="error" class="tbe-error" role="alert" data-testid="task-body-error">{{ error }}</p>
    </template>

    <MarkdownContent
      v-else-if="task.body" class="tbe-body" :markdown="rendered" refs
      :project="task.project_id" data-testid="task-body-view"
    />
    <p v-else class="tbe-empty" data-testid="task-body-empty">
      {{ emptyText || 'No details yet.' }}
    </p>
  </Card>
</template>

<style scoped>
.tbe-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
}
.tbe-title { margin-bottom: 0; }
.tbe-editor { --me-head-bleed: 1.25rem; }
.tbe-hint {
    margin-top: 0.375rem;
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
}
.tbe-error {
    margin-top: 0.375rem;
    font-size: 0.8125rem;
    color: var(--color-danger);
    overflow-wrap: anywhere;
}
.tbe-body {
    margin-top: 0.75rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    overflow-wrap: anywhere;
}
.tbe-body :deep(img) {
    max-width: 100%;
    height: auto;
    border-radius: var(--radius-md);
}
.tbe-empty {
    margin-top: 0.5rem;
    font-size: 0.8125rem;
    color: var(--color-fg-faint);
}
</style>
