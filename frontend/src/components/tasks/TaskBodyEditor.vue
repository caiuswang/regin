<script setup>
// The task body, read and written on the page that has its address.
//
// Source over a rendered preview, not a rich editor. The column holds a
// feature's whole spec and agents both read and write it, so the editor hands
// back the exact bytes it was given — a WYSIWYG round trip was measured
// (regin:67) at 11% of lines rewritten, and it deletes HTML comments.
//
// Shaped after `PatternContentEditor`, the same read-then-Edit toggle.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageFilesFrom } from '../../utils/imageFiles'
import { IMAGE_REF_HINT, imageRefBlock, resolveImageRefs } from '../../utils/taskBodyImages'

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
// Source is the editing mode, for the reason the file header states. Preview
// is a READ of the draft, not a second editor: an `![](image:N)` reference is
// a number until something resolves it, and checking that the number names the
// picture you meant used to mean saving first.
const previewing = ref(false)
const draft = ref('')
const saving = ref(false)
const error = ref('')
const area = ref(null)

const rendered = computed(() => resolveImageRefs(props.task.body, props.images))
const draftRendered = computed(() => resolveImageRefs(draft.value, props.images))

function start() {
  draft.value = props.task.body || ''
  error.value = ''
  previewing.value = false
  editing.value = true
}

function cancel() {
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
  const refs = imageRefBlock(await props.attach(source))
  if (!refs) return
  draft.value = withCaretInsert(caretTarget(event, area.value), draft.value, refs)
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
      <div v-else-if="editing" class="tbe-actions">
        <Button
          variant="ghost" size="sm" :aria-pressed="previewing"
          data-testid="task-body-preview" @click="previewing = !previewing"
        >
          <Icon :name="previewing ? 'pencil' : 'eye'" :size="13" />
          <span>{{ previewing ? 'Source' : 'Preview' }}</span>
        </Button>
        <Button
          variant="primary" size="sm" :disabled="saving"
          data-testid="task-body-save" @click="save"
        >{{ saving ? 'Saving…' : 'Save' }}</Button>
        <Button variant="secondary" size="sm" data-testid="task-body-cancel" @click="cancel">
          Cancel
        </Button>
      </div>
    </div>

    <template v-if="editing">
      <!-- v-show, not v-if: the textarea has to keep its caret and its scroll
           position across a preview, or toggling back drops the operator at
           the top of a spec they were editing the middle of. -->
      <RefTextarea
        v-show="!previewing"
        ref="area"
        v-model="draft"
        :project="task.project_id"
        :rows="18"
        class="tbe-area"
        spellcheck="false"
        aria-label="Task details markdown"
        data-testid="task-body-area"
        @paste="onPaste"
        @dragover.prevent
        @drop="onDrop"
      />
      <MarkdownContent
        v-if="previewing" class="tbe-body" :markdown="draftRendered"
        data-testid="task-body-preview-pane"
      />
      <p class="tbe-hint">Markdown. {{ IMAGE_REF_HINT }} Paste or drop an image here.</p>
      <p v-if="error" class="tbe-error" role="alert" data-testid="task-body-error">{{ error }}</p>
    </template>

    <MarkdownContent v-else-if="task.body" class="tbe-body" :markdown="rendered" />
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
.tbe-actions { display: flex; gap: 0.375rem; }
/* The primitive already brings the token-backed `.input` box; this adds only
   what a markdown source view needs on top of it. */
.tbe-area {
    margin-top: 0.75rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem;
    line-height: 1.5;
}
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
